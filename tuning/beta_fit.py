"""
beta_fit.py — SSRFB（実体は dtsmqr、kernel_bench.py 参照）を計測して β を推定する。

計測点: nb ∈ {64, 128, 256, 384, 512}、各 nb について ib <= 64 の範囲を
2刻みで1点ずつ計測し（kernel_bench.py 参照。1点=1プロセス起動）、最大 GFlops
（MaxIB と同じ考え方）を採用する。--beta-reps は NoFlush 内部の平均化回数
（N_IT）に渡す（既定50。dtsmqr グリッド計測の kernel_reps や参照データ
（ssrfb.parquet, N_IT=50）と揃える。低い値だとキャッシュコールドの1発だけを
測ることになり β が過大評価される。2026-09-01 ユーザ指摘・実測で確認。
kernel_bench.py 冒頭の理由も参照）。

GFlops = G_inf・nb/(nb+β) を最小二乗フィットして β を返す。
フィットは scipy を使わず、自前の1次元探索で行う:
    β を対数グリッドで走査し、各 β について G_inf は最小二乗の解析解
    （w_i = nb_i/(nb_i+β) として G_inf = Σ(w_i・g_i) / Σ(w_i^2)）で求める。
理由: 計測コア（src/）を標準ライブラリのみで動かす既存方針を崩さないため。
scipy を入れるくらいならグリッド探索の方が移植性が高い（指示書 3.2）。
"""
import csv
import os
import time

from .kernel_bench import run_dtsmqr_sweep, flops_dtsmqr

BETA_NB_POINTS = (64, 128, 256, 384, 512)
IB_MAX_FOR_BETA = 64


def measure_maxib_gflops(nb: int, threads: int, reps: int = 50, exe: str = None):
    """nb 固定で ib<=64 を計測し、MaxIB（最大 GFlops）の (ib, gflops) を返す。

    reps は NoFlush 内部の平均化回数（N_IT）に渡す。1点=1プロセス起動で
    完結する（kernel_bench.py 冒頭の理由を参照。単発の外部反復ではなく
    NoFlush 自身の N_IT 回実行の平均を使う）。
    有効な ib が一つも無い場合は (None, 0.0, n_launches)。

    Returns
    -------
    (best_ib, best_gflops, n_launches)
    """
    ib_lo = 2
    ib_hi = min(IB_MAX_FOR_BETA, nb // 2)

    times, n_launches = run_dtsmqr_sweep(nb, ib_lo, ib_hi, threads=threads, exe=exe, n_it=reps)

    best_ib, best_gflops = None, 0.0
    for ib, t in times.items():
        g = flops_dtsmqr(nb, ib) / t / 1e9
        if g > best_gflops:
            best_gflops, best_ib = g, ib

    return best_ib, best_gflops, n_launches


def estimate_n_launches(nb_points=None, ib_max: int = IB_MAX_FOR_BETA, ib_step: int = 2) -> int:
    """β計測が起動する NoFlush プロセス数の見積り（--dry-run 用）。"""
    if nb_points is None:
        nb_points = BETA_NB_POINTS
    total = 0
    for nb in nb_points:
        ib_hi = min(ib_max, nb // 2)
        total += len(range(2, ib_hi + 1, ib_step))
    return total


def _fit_beta_ginf(nb_values, gflops_values, beta_grid=None):
    """β を対数グリッドで走査し、残差二乗和最小の (beta, g_inf) を返す。

    各 β での G_inf は解析解（重み付き最小二乗、重み w_i = nb_i/(nb_i+β)）:
        G_inf(beta) = argmin_A Σ (A・w_i - g_i)^2 = Σ(w_i・g_i) / Σ(w_i^2)
    """
    if beta_grid is None:
        # beta ≈ 0.1 〜 3000 を対数グリッドで走査
        beta_grid = [10 ** (e / 20.0) for e in range(-20, 71)]

    best_beta, best_ginf, best_sse = None, None, float("inf")
    for beta in beta_grid:
        ws = [nb / (nb + beta) for nb in nb_values]
        denom = sum(w * w for w in ws)
        if denom <= 0:
            continue
        ginf = sum(w * g for w, g in zip(ws, gflops_values)) / denom
        sse = sum((ginf * w - g) ** 2 for w, g in zip(ws, gflops_values))
        if sse < best_sse:
            best_sse, best_beta, best_ginf = sse, beta, ginf

    return best_beta, best_ginf


def fit_beta(
    threads: int,
    reps: int = 50,
    exe: str = None,
    nb_points=BETA_NB_POINTS,
    raw_csv: str = None,
):
    """SSRFB(dtsmqr代理) を計測して β を推定する。

    Returns
    -------
    dict: {
        "beta": float | None, "g_inf": float | None,
        "time_sec": float,            # 計測にかかった実時間（1.1 のため）
        "n_kernel_exec": int,         # 実際に起動した NoFlush プロセス数
        "raw": [{"nb", "ib", "gflops"}, ...],
    }
    """
    t0 = time.time()
    raw = []
    n_kernel_exec = 0
    for nb in nb_points:
        best_ib, best_gflops, n_launches = measure_maxib_gflops(nb, threads, reps=reps, exe=exe)
        n_kernel_exec += n_launches
        raw.append({"nb": nb, "ib": best_ib, "gflops": best_gflops})
    elapsed = time.time() - t0

    valid = [r for r in raw if r["gflops"] > 0]
    nb_values = [r["nb"] for r in valid]
    gflops_values = [r["gflops"] for r in valid]

    beta, g_inf = (None, None)
    if len(valid) >= 2:
        beta, g_inf = _fit_beta_ginf(nb_values, gflops_values)

    if raw_csv:
        os.makedirs(os.path.dirname(raw_csv), exist_ok=True)
        write_header = not os.path.exists(raw_csv)
        with open(raw_csv, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["nb", "ib", "gflops"])
            for r in raw:
                writer.writerow([r["nb"], r["ib"], r["gflops"]])

    return {
        "beta": beta,
        "g_inf": g_inf,
        "time_sec": elapsed,
        "n_kernel_exec": n_kernel_exec,
        "raw": raw,
    }
