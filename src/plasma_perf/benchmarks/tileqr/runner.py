"""runner.py — tileqr（PLASMA dgeqrf）の1点実行アダプタ。

plasmatest dgeqrf を1回呼び、出力から GFlops をパースする。plasmatest のパスは
--exe か環境変数 PLASMA_TEST（未指定時は ~/Library/plasma/bin/plasmatest）。

計測（core.sweep）とチューニング（tuning.*）は、どちらもこの run_point /
measure_gflops を通るため、同一の実行経路になる。
"""
from __future__ import annotations

import os
import re
from typing import Optional

from ..base import Benchmark, PointResult
from ...core.env import build_env
from ...core.process import run_process

_PLASMA_TEST_DEFAULT = os.path.expanduser("~/Library/plasma/bin/plasmatest")

# plasmatest の "pass <err1> <err2> <gflops>" 行から GFlops（3番目）を取り出す。
_PASS_RE = re.compile(r"pass\s+([-\d.eE]+)\s+([-\d.eE]+)\s+([\d.]+)")


def resolve_exe(exe: Optional[str] = None) -> str:
    return exe or os.environ.get("PLASMA_TEST", _PLASMA_TEST_DEFAULT)


def parse_gflops(stdout: str) -> Optional[float]:
    match = _PASS_RE.search(stdout)
    return float(match.group(3)) if match else None


class TileQRBenchmark(Benchmark):
    name = "tileqr"
    tag = ""  # ファイル名にタグは付けない（既存ダッシュボードと互換）
    value_fields = ["GFlops"]

    def __init__(self, exe: Optional[str] = None):
        self.exe = resolve_exe(exe)

    def make_env(self, threads: int) -> dict:
        # 従来 run_time_dgeqrf の挙動を保持: OMP_NUM_THREADS のみ設定
        # （PLASMA が並列化するため BLAS のピン留めや LD_PRELOAD 除去はしない）。
        return build_env(threads, pin_blas=False, drop_ld_preload=False)

    def run_point(
        self, nb, ib, *, threads, size, env, timeout, wrapper
    ) -> PointResult:
        command = [
            self.exe,
            "dgeqrf",
            f"--dim={size}x{size}",
            f"--nb={nb}",
            f"--ib={ib}",
        ]
        proc = run_process(command, env=env, timeout=timeout, wrapper=wrapper)
        gflops = parse_gflops(proc.stdout) if proc.returncode == 0 else None
        ok = gflops is not None and gflops > 0
        # 欠測は 0.0 で記録する（旧 tileQR_bench との CSV 互換）。
        # 内部的には ok=False として欠測カウント・生ログ保存の対象になる。
        return PointResult(
            values={"GFlops": gflops if ok else 0.0},
            ok=ok,
            proc=proc,
        )


def measure_gflops(
    nb: int,
    ib: int,
    *,
    threads: int,
    size: int,
    exe: Optional[str] = None,
    timeout: Optional[float] = None,
) -> float:
    """チューニング用: (nb, ib) の GFlops を返す（失敗時は 0.0）。

    Optuna の目的関数などから呼ぶ。計測スイープと同じ run_point を通る。
    """
    bench = TileQRBenchmark(exe=exe)
    env = bench.make_env(threads)
    result = bench.run_point(
        nb, ib, threads=threads, size=size, env=env, timeout=timeout, wrapper=None
    )
    return float(result.values["GFlops"]) if result.ok else 0.0
