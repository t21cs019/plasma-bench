"""runner.py — NoFlush を複数コアに1本ずつ固定して同時実行する。

1コア計測（benchmarks/ssrfb）との違い:
  - 1点 (nb, ib) ごとに、選んだ全コアへ `taskset -c <cpu> NoFlush nb ib MAT_SIZE N_IT`
    を一斉に起動し、全プロセスの終了を待ってから次の点へ進む（点ごとに足並みを揃える）。
    本数が途中で減ったり、別の (nb, ib) と競合したりすることがない。
  - N_IT=50 のままだと小さい nb では計時区間がミリ秒以下で終わり、プロセス間で
    計時区間が重ならない（起動・行列初期化のずれの方が長い）。そこで N_IT を
    「計時区間がおよそ target_sec 秒になる」よう点ごとに決める（下限 n_it_min）。
      * nb の最初の ib: 1本だけ N_IT=n_it_min で走らせて1回あたりの時間を見積もる
      * 以降の ib: 直前の点の同時実行で得た1回あたり時間（最も遅いコア）から決める
  - 計時区間の重なり具合の目安として、プロセス終了時刻のばらつき（end_skew_sec）を記録する。

NoFlush 本体・出力契約・flops 式は benchmarks/ssrfb と共通（そちらから import する）。
"""
from __future__ import annotations

import math
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from ..ssrfb.runner import flops_ssrfb, parse_time
from ...core.env import build_env
from ...core.timeout import estimate_timeout_sec


@dataclass
class CoreResult:
    """1コア分の結果。time_sec は1回あたり平均秒（失敗時 None）。"""

    cpu: int
    time_sec: Optional[float]
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    t_end: float


# --- CPU の選び方 -----------------------------------------------------------------

def parse_lscpu(text: str) -> List[Dict[str, int]]:
    """`lscpu -p=CPU,CORE,SOCKET,NODE` の出力を [{cpu, core, socket, node}, ...] にする。

    NODE が空（NUMA 情報なし）の行は node=-1 とする。
    """
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split(",")
        cpu, core, sock = int(fields[0]), int(fields[1]), int(fields[2])
        node = int(fields[3]) if len(fields) > 3 and fields[3] != "" else -1
        rows.append({"cpu": cpu, "core": core, "socket": sock, "node": node})
    return rows


def socket_cpus(rows: Sequence[Dict[str, int]], socket_id: int,
                allowed: Optional[Sequence[int]] = None) -> List[int]:
    """socket_id の物理コアごとに論理CPUを1つ（番号が最小のもの）選んで返す。

    SMT の兄弟スレッドは除く。allowed（現在の affinity）に無い CPU も除く。
    CPU 番号がソケット間で交互に振られている機種でも、lscpu の SOCKET 列で選ぶので正しい。
    """
    allowed_set = set(allowed) if allowed is not None else None
    first: Dict[int, int] = {}
    for r in rows:
        if r["socket"] != socket_id:
            continue
        if allowed_set is not None and r["cpu"] not in allowed_set:
            continue
        if r["core"] not in first or r["cpu"] < first[r["core"]]:
            first[r["core"]] = r["cpu"]
    return sorted(first.values())


def detect_socket_cpus(socket_id: int = 0) -> List[int]:
    out = subprocess.run(
        ["lscpu", "-p=CPU,CORE,SOCKET,NODE"], capture_output=True, text=True, check=True
    ).stdout
    return socket_cpus(parse_lscpu(out), socket_id, allowed=sorted(os.sched_getaffinity(0)))


def parse_cpu_list(spec: str) -> List[int]:
    """"0-3,8,10-11" 形式を [0,1,2,3,8,10,11] にする。"""
    cpus: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-")
            cpus.extend(range(int(lo), int(hi) + 1))
        else:
            cpus.append(int(part))
    return sorted(set(cpus))


# --- N_IT の決め方 ----------------------------------------------------------------

def choose_n_it(per_iter_sec: Optional[float], target_sec: float, n_it_min: int) -> int:
    """計時区間がおよそ target_sec 秒になる N_IT（下限 n_it_min）。見積もり不能なら n_it_min。"""
    if per_iter_sec is None or per_iter_sec <= 0:
        return n_it_min
    return max(n_it_min, int(math.ceil(target_sec / per_iter_sec)))


# --- 実行 -------------------------------------------------------------------------

def make_env() -> dict:
    # 1コア計測と同じ: 各プロセスは1スレッド、BLAS のネスト並列を抑止、LD_PRELOAD を除去
    return build_env(1, pin_blas=True, drop_ld_preload=True)


def run_parallel_point(
    exe: str,
    nb: int,
    ib: int,
    *,
    size: int,
    n_it: int,
    cpus: Sequence[int],
    env: dict,
    timeout: Optional[float],
    wrapper: Optional[str] = None,
) -> List[CoreResult]:
    """cpus の各コアに NoFlush を1本ずつ固定して同時に起動し、全部の終了を待つ。

    1本でもタイムアウトしたら残りも kill する（その点は全コア欠測になる）。
    """
    prefix = shlex.split(wrapper) if wrapper else []
    command = [exe, str(nb), str(ib), str(size), str(n_it)]

    procs = []
    for cpu in cpus:
        procs.append((cpu, subprocess.Popen(
            prefix + ["taskset", "-c", str(cpu)] + command,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env,
        )))

    deadline = None if timeout is None else time.monotonic() + timeout
    results: List[CoreResult] = []
    for cpu, p in procs:
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            stdout, stderr = p.communicate(timeout=remaining)
            timed_out = False
        except subprocess.TimeoutExpired:
            for _, q in procs:
                q.kill()
            stdout, stderr = p.communicate()
            timed_out = True
        t_end = time.monotonic()
        time_sec = parse_time(stdout) if (p.returncode == 0 and not timed_out) else None
        results.append(CoreResult(
            cpu=cpu, time_sec=time_sec if time_sec and time_sec > 0 else None,
            returncode=None if timed_out else p.returncode,
            stdout=stdout or "", stderr=stderr or "", timed_out=timed_out, t_end=t_end,
        ))
    return results


def end_skew_sec(results: Sequence[CoreResult]) -> float:
    """最初に終わったプロセスと最後に終わったプロセスの差（秒）。

    communicate を順に待つので、先に終わったプロセスの t_end は実際より遅く見える
    （＝過小評価側に倒れる）。計時区間の重なりの粗い目安として使う。
    """
    ends = [r.t_end for r in results]
    return max(ends) - min(ends) if ends else 0.0


def point_timeout(nb: int, ib: int, n_it: int, assumed_gflops: float) -> float:
    return estimate_timeout_sec(flops_ssrfb, nb, ib, assumed_gflops, n_it=n_it)
