"""timeout.py — 1点あたりのタイムアウト秒の保守的な見積もり。

flops_fn(nb, ib) と「実機はこの GFlops を下回らないはず」という下限想定値から
1点の予測時間を出し、その factor 倍をタイムアウトとする。N_IT のように内部で
反復する実行ファイル向けに n_it を掛けられる（ssrfb=50000, tileqr=1）。
"""
from typing import Callable


def estimate_timeout_sec(
    flops_fn: Callable[[int, int], float],
    nb: int,
    ib: int,
    assumed_gflops: float = 5.0,
    n_it: int = 1,
    min_timeout: float = 60.0,
    factor: float = 3.0,
) -> float:
    predicted_iter_sec = flops_fn(nb, ib) / (assumed_gflops * 1e9)
    predicted_total_sec = predicted_iter_sec * n_it
    return max(min_timeout, predicted_total_sec * factor)
