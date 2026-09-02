"""
agullo.py — 卒論の既存手法（Agullo LAWN 242 ヒューリスティック2）の再現。

手順（指示書 3.3）:
    1. dtsmqr を nb×ib で計測する（卒論は50回実行して平均。--kernel-reps 既定50）
    2. 各 nb について最高性能の ib を選ぶ → MaxIB ペア
    3. ヒューリスティック2で PS ペアを8組選ぶ
    4. 8組それぞれで QR ベンチを実行し、最良を返す

dtsmqr の叩き方は kernel_bench.py（NoFlush 経由）を参照。実機の NoFlush は
1起動につき (nb, ib) 1点のみを返す契約（kernel_bench.py 冒頭で検証済み）なので、
ib は 2〜nb/2 を DEFAULT_IB_STEP=2 刻みで1点ずつ起動して計測する（nb の約数に
限定されないため、指示書の「ib step=2」をそのまま再現できる）。
--kernel-reps は NoFlush 内部の平均化回数（N_IT）に渡す（kernel_bench.py
冒頭の理由により、外部プロセス反復ではなくこちらを採用）。

dtsmqr カーネル計測は常に kernel_threads（既定1）で実行する。既存の
src/plasma_perf/benchmarks/cli.py の _DEFAULT_THREADS = {"tileqr": os.cpu_count(),
"ssrfb": 1} に合わせた（campaign の --threads はフルQR測定=手順4用で、
dtsmqrカーネル計測とは独立。2026-09-01 ユーザ指摘により修正: 以前は誤って
--threads をそのまま dtsmqr 計測に渡していた）。

--- ヒューリスティック2 条件2・3の解釈（2026-09-01 ユーザ確認: 妥当な解釈で実装し
notes に明記してよいとの回答を得た）---

Agullo LAWN 242 原論文・卒論本文がこのワークスペースから参照できず、条件の
数式的定義を一意に特定できなかったため、以下の解釈で実装する:

    条件1（凸包上）: MaxIB ペア (nb, gflops) の上側凸包上にある点。
    条件2（傾きが最も大きい）: 凸包の隣接頂点間のセグメントについて
        slope_i = (gflops[i] - gflops[i-1]) / (nb[i] - nb[i-1])
        を計算し、各 nb 分割区間（条件3）内ではこの slope が最大になる
        セグメントの「到達側」の頂点を選ぶ（＝その区間内で最も急激に
        性能が伸びた直後の凸包点 = 効率が良い"knee"点とみなす）。
    条件3（nb 軸を8等分）: MaxIB ペア全体の nb の最小値〜最大値を等幅8区間に
        分割し、区間ごとに条件1・2を満たす点を1つ選ぶ（該当点が無い区間は
        スキップし、選択数が8未満になる場合がある。notes に選択数を記録）。

凸包は scipy.spatial を使わず、単調鎖法（monotone chain, 上側凸包のみ）を
自前で実装する。
"""
import os
import sys
import time

from .. import kernel_bench

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
from plasma_perf.benchmarks.tileqr.runner import measure_gflops  # noqa: E402

DEFAULT_NB_MIN = 4
DEFAULT_NB_MAX = 512
DEFAULT_NB_STEP = 4
DEFAULT_IB_STEP = 2
DEFAULT_KERNEL_REPS = 50
DEFAULT_KERNEL_THREADS = 1
N_BINS = 8


def n_ib_points(nb: int, ib_step: int = DEFAULT_IB_STEP) -> int:
    """指定 nb で計測する ib 点数（2〜nb/2 を ib_step 刻み）。dry-run 見積り・
    実測の両方で使う共通ロジック。"""
    ib_hi = max(2, nb // 2)
    return len(range(2, ib_hi + 1, ib_step))


def estimate_n_launches(nb_min: int, nb_max: int, nb_step: int, ib_step: int = DEFAULT_IB_STEP) -> int:
    """dtsmqr グリッド計測が起動する NoFlush プロセス数の見積り（--dry-run 用）。"""
    return sum(n_ib_points(nb, ib_step) for nb in range(nb_min, nb_max + 1, nb_step))


def measure_dtsmqr_grid(
    nb_min: int,
    nb_max: int,
    nb_step: int,
    threads: int,
    kernel_reps: int,
    ib_step: int = DEFAULT_IB_STEP,
    exe: str = None,
):
    """dtsmqr を nb×ib で計測する。

    kernel_reps は NoFlush 内部の平均化回数（N_IT）に渡す（1点=1プロセス起動。
    モジュール冒頭・kernel_bench.py 冒頭の理由を参照）。

    Returns
    -------
    (maxib_pairs, n_kernel_exec)
        maxib_pairs: [(nb, best_ib, best_gflops), ...]（nb 昇順）
        n_kernel_exec: NoFlush サブプロセス起動回数
    """
    maxib_pairs = []
    n_kernel_exec = 0

    for nb in range(nb_min, nb_max + 1, nb_step):
        ib_hi = max(2, nb // 2)
        times, n_launches = kernel_bench.run_dtsmqr_sweep(
            nb, 2, ib_hi, ib_step=ib_step, threads=threads, exe=exe, n_it=kernel_reps
        )
        n_kernel_exec += n_launches

        best_ib, best_gflops = None, 0.0
        for ib, t in times.items():
            g = kernel_bench.flops_dtsmqr(nb, ib) / t / 1e9
            if g > best_gflops:
                best_gflops, best_ib = g, ib

        if best_ib is not None:
            maxib_pairs.append((nb, best_ib, best_gflops))

    return maxib_pairs, n_kernel_exec


def upper_convex_hull(points):
    """単調鎖法（monotone chain）による上側凸包。points: [(x, y), ...]（x昇順・重複無し前提）。

    Returns
    -------
    上側凸包を x 昇順に並べたリスト [(x, y), ...]
    """
    pts = sorted(points, key=lambda p: (p[0], p[1]))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    upper = []
    for p in pts:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) >= 0:
            upper.pop()
        upper.append(p)
    return upper


def select_heuristic2(maxib_pairs, n_bins: int = N_BINS):
    """ヒューリスティック2で PS ペアを最大 n_bins 組選ぶ。

    Returns
    -------
    (selected, note)
        selected: [(nb, ib, gflops), ...]
        note: str（選択数や解釈の断り書き）
    """
    if len(maxib_pairs) < 2:
        return list(maxib_pairs), "MaxIBペアが2点未満のため凸包計算をスキップした"

    by_nb = {nb: (ib, g) for nb, ib, g in maxib_pairs}
    points = [(nb, g) for nb, ib, g in maxib_pairs]
    hull = upper_convex_hull(points)  # x(=nb) 昇順

    if len(hull) < 2:
        selected = [(nb, by_nb[nb][0], g) for nb, g in hull]
        return selected, "凸包が1点のみのため条件2以降は適用できなかった"

    nb_all = [nb for nb, _, _ in maxib_pairs]
    nb_lo, nb_hi = min(nb_all), max(nb_all)
    bin_width = (nb_hi - nb_lo) / n_bins if nb_hi > nb_lo else 1

    def bin_index(nb):
        if bin_width == 0:
            return 0
        idx = int((nb - nb_lo) / bin_width)
        return min(idx, n_bins - 1)

    # 各凸包頂点（2番目以降）の「到達側」の傾きを計算
    slopes = []  # (nb, gflops, slope)
    for i in range(1, len(hull)):
        nb_prev, g_prev = hull[i - 1]
        nb_cur, g_cur = hull[i]
        slope = (g_cur - g_prev) / (nb_cur - nb_prev)
        slopes.append((nb_cur, g_cur, slope))
    # 先頭点も候補に含める（傾き比較対象がないので slope=0 扱い）
    slopes.append((hull[0][0], hull[0][1], 0.0))

    best_per_bin = {}
    for nb, g, slope in slopes:
        b = bin_index(nb)
        if b not in best_per_bin or slope > best_per_bin[b][2]:
            best_per_bin[b] = (nb, g, slope)

    selected = []
    for b in sorted(best_per_bin):
        nb, g, _slope = best_per_bin[b]
        ib = by_nb[nb][0]
        selected.append((nb, ib, g))

    note = f"heuristic2: hull_points={len(hull)}, bins_filled={len(selected)}/{n_bins}."
    return selected, note


def run(size, threads, seed, ctx=None, **kwargs):
    """
    Parameters
    ----------
    ctx : dict | None
        run_tuning.py から渡される共有コンテキスト（未使用時は無視してよい）
    kwargs:
        nb_min, nb_max, nb_step : dtsmqr スイープ範囲（既定 4,512,4）
        kernel_reps : dtsmqr の反復回数（既定50。NoFlush の N_IT に渡る）
        kernel_threads : dtsmqr 計測に使うスレッド数（既定1。threads[QR用]とは
                      独立。モジュール冒頭参照）
        exe : NoFlush パス上書き

    Returns
    -------
    dict: nb, ib, time_total_sec, time_prep_sec, n_qr_exec, n_kernel_exec,
          beta, band_lo, band_hi, gflops_observed, notes
    """
    nb_min = kwargs.get("nb_min", DEFAULT_NB_MIN)
    nb_max = kwargs.get("nb_max", DEFAULT_NB_MAX)
    nb_step = kwargs.get("nb_step", DEFAULT_NB_STEP)
    ib_step = kwargs.get("ib_step", DEFAULT_IB_STEP)
    kernel_reps = kwargs.get("kernel_reps", DEFAULT_KERNEL_REPS)
    kernel_threads = kwargs.get("kernel_threads", DEFAULT_KERNEL_THREADS)
    exe = kwargs.get("exe")

    t0 = time.time()
    maxib_pairs, n_kernel_exec = measure_dtsmqr_grid(
        nb_min, nb_max, nb_step, kernel_threads, kernel_reps, ib_step=ib_step, exe=exe
    )
    time_prep = time.time() - t0

    selected, note = select_heuristic2(maxib_pairs)

    if nb_min != DEFAULT_NB_MIN or nb_max != DEFAULT_NB_MAX:
        note += (
            f" | DEVIATION from thesis: nb range overridden to [{nb_min},{nb_max}] "
            f"(thesis default [{DEFAULT_NB_MIN},{DEFAULT_NB_MAX}]). Small nb makes "
            f"PLASMA's task count blow up as (size/nb)^3, which OOM'd this host at nb=4."
        )

    n_qr_exec = 0
    best_nb, best_ib, best_gflops = None, None, -1.0
    t1 = time.time()
    for nb, ib, _g in selected:
        gflops = measure_gflops(nb, ib, threads=threads, size=size, exe=None)
        n_qr_exec += 1
        if gflops > best_gflops:
            best_gflops, best_nb, best_ib = gflops, nb, ib
    qr_elapsed = time.time() - t1

    total_elapsed = time_prep + qr_elapsed

    return {
        "nb": best_nb,
        "ib": best_ib,
        "time_total_sec": total_elapsed,
        "time_prep_sec": time_prep,
        "n_qr_exec": n_qr_exec,
        "n_kernel_exec": n_kernel_exec,
        "beta": None,
        "band_lo": None,
        "band_hi": None,
        "gflops_observed": best_gflops if best_gflops >= 0 else None,
        "notes": note,
    }
