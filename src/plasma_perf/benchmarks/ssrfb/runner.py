"""runner.py — ssrfb（DSSRFB/DTSMQR カーネル単体, NoFlush）の1点実行アダプタ。

NoFlush の入出力契約:
    usage: NoFlush [NB size] [IB size]
    出力: "nb, ib, time" 形式 1 行（例: 256, 64, 0.00234831）
    time は C++ 内部タイマーによる N_IT 回（既定 50000 回, C++側ハードコード）の
    平均実行時間（秒）。

NoFlush のパスは --exe か環境変数 NOFLUSH_PATH
（未指定時は ~/Library/Tune_SSRFB/NoFlush）。
"""
from __future__ import annotations

import os
import re
from typing import Optional

from ..base import Benchmark, PointResult
from ...core.env import build_env
from ...core.process import run_process
from ...core.timeout import estimate_timeout_sec

# NoFlush.cpp の既定反復回数。実行時引数（--n-it）で変更できる。
N_IT_DEFAULT = 50

_NOFLUSH_DEFAULT = os.path.expanduser("~/Library/Tune_SSRFB/NoFlush")

_OUTPUT_RE = re.compile(
    r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.eE+-]+)\s*$", re.MULTILINE
)


def resolve_exe(exe: Optional[str] = None) -> str:
    return exe or os.environ.get("NOFLUSH_PATH", _NOFLUSH_DEFAULT)


def flops_ssrfb(nb: int, ib: int) -> int:
    """DSSRFB 主要3ステップの支配項 4*nb^3（Buttari et al. arXiv:0709.1272 §3.2）。
    ib 依存の低次項（nb^2*ib オーダー）は既定では無視する。"""
    return 4 * nb ** 3


def parse_time(stdout: str) -> Optional[float]:
    match = _OUTPUT_RE.search(stdout)
    return float(match.group(3)) if match else None


class SSRFBBenchmark(Benchmark):
    name = "ssrfb"
    tag = "ssrfb"
    value_fields = ["Time_sec", "GFlops"]

    def __init__(self, exe: Optional[str] = None, n_it: int = N_IT_DEFAULT):
        self.exe = resolve_exe(exe)
        self.n_it = n_it

    def make_env(self, threads: int) -> dict:
        # カーネル単体計測: BLAS を1スレッドに固定し、LD_PRELOAD を除去。
        return build_env(threads, pin_blas=True, drop_ld_preload=True)

    def run_point(
        self, nb, ib, *, threads, size, env, timeout, wrapper
    ) -> PointResult:
        # NoFlush [NB] [IB] [MAT_SIZE] [N_IT]。size を MAT_SIZE として渡す
        # （キャッシュ占有量に影響。CSV の size 列にも記録される）。
        command = [self.exe, str(nb), str(ib), str(size), str(self.n_it)]
        proc = run_process(command, env=env, timeout=timeout, wrapper=wrapper)
        time_sec = parse_time(proc.stdout) if proc.returncode == 0 else None
        ok = time_sec is not None and time_sec > 0
        if ok:
            gflops = flops_ssrfb(nb, ib) / time_sec / 1e9
            values = {"Time_sec": time_sec, "GFlops": gflops}
        else:
            values = {"Time_sec": -1.0, "GFlops": float("nan")}
        return PointResult(values=values, ok=ok, proc=proc)

    def estimate_timeout(self, nb, ib, assumed_gflops):
        return estimate_timeout_sec(
            flops_ssrfb, nb, ib, assumed_gflops, n_it=self.n_it
        )


def measure_time(
    nb: int,
    ib: int,
    *,
    exe: Optional[str] = None,
    threads: int = 1,
    mat_size: int = 4096,
    n_it: int = N_IT_DEFAULT,
    timeout: Optional[float] = None,
) -> float:
    """チューニング用: (nb, ib) の1回あたり平均実行時間（秒）を返す（失敗時 inf）。"""
    bench = SSRFBBenchmark(exe=exe, n_it=n_it)
    env = bench.make_env(threads)
    result = bench.run_point(
        nb, ib, threads=threads, size=mat_size, env=env, timeout=timeout, wrapper=None
    )
    return float(result.values["Time_sec"]) if result.ok else float("inf")
