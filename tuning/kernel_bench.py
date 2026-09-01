"""
kernel_bench.py — dtsmqr カーネル単体計測ラッパー（NoFlush 経由）。

叩き方について（2026-09-01 ユーザ確認・実機で検証済み）:
    plasmatest には dtsmqr/ssrfb 相当のルーチンが存在しない
    （LAPACK レベルの dgeqrf 等のみ露出しており、PLASMA 内部の
    core_dtsmqr / core_dssrfb は叩けない）。

    卒論当時に kernel_dtsmqr.parquet を生成した実際のツールはこのワークスペース
    内には見つからなかった。代わりに optuna_tileQR/tools/Tune_SSRFB-master の
    NoFlush（Tune_SSRFB, https://github.com/stomo0511/Tune_SSRFB）を使う。
    NoFlush は "SSRFB" と名付けられているが、ソース（NoFlush.cpp）の実体は
    plasma_core_dtsmqr を呼んでいる。そのため agullo（dtsmqr ベンチ）と
    model（SSRFB によるβ推定）は実際には同じカーネル呼び出しを利用する。

    このリポジトリは既にモノレポ再構成（コミット 9e40e54）を経ており、NoFlush
    を叩く「既存の計測経路」は src/plasma_perf/benchmarks/ssrfb/runner.py に
    実装済み。本モジュールはそれをそのまま再利用する
    （指示書6節「既存のsrc/を壊さない・importして使うだけ」に従う。車輪の
    再発明をしない）。

    NoFlush の実際の CLI 契約（plasma-workspace/tune_ssrfb/NoFlush.cpp を確認、
    実機バイナリ ~/Library/Tune_SSRFB/NoFlush で実行して検証済み）:
        usage: NoFlush [NB] [IB] [MAT_SIZE=4096] [N_IT=50]
        標準出力: "nb, ib, time" の1行のみ（time は N_IT 回実行の平均秒）。
        1回の起動につき (nb, ib) 1点のみ計測できる。

    この草稿の旧バージョンは「1起動で ib 範囲を一括計測できる」
    （usage: NoFlush [NB] [min IB] [max IB]、N_IT/MAT_SIZE はビルド時定数）という
    別バージョンの NoFlush を前提にしていたが、実機のバイナリでは動かない
    （試しに旧仕様のコマンドを実行すると ib_max が MAT_SIZE として渡され
    "MAT_SIZE^2 < 3*nb*nb" エラーになることを確認した）。ib 範囲の計測は
    このモジュール側で (nb, ib) を1点ずつ順に起動するループに変更した。

    --kernel-reps / --beta-reps（卒論の「50回実行して平均」）について:
    外部から Python で NoFlush プロセスを50回起動して平均する実装にすると、
    agullo のフルグリッド計測（nb約127点×ib約64点）だけで数十万プロセス
    起動になり非現実的（指示書5節の見積り約80分を大幅に超過する）。
    NoFlush 自身が N_IT 引数で「カーネルを N_IT 回実行して平均」を既に内部で
    行うため、reps はそのまま N_IT に渡す（2026-09-01 ユーザ確認）。
    1点 = 1プロセス起動で完結し、指示書の想定時間に近い規模を維持できる。

flops_dtsmqr は SSRFB（DSSRFB）の支配項 4*nb^3（Buttari et al.
arXiv:0709.1272 §3.2）を暫定的に流用している（dtsmqr 固有の flops 式は
未検証・要検証）。既存の flops_ssrfb と同一式なので、二重実装を避けて
そのまま再利用する。
"""
import os
import sys

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from plasma_perf.benchmarks.ssrfb.runner import (  # noqa: E402
    N_IT_DEFAULT,
    flops_ssrfb,
    measure_time,
)

# NoFlush 自身の既定値をそのまま使う。dtsmqr/SSRFB カーネル計測の MAT_SIZE は
# QR 問題（--size）とは独立（β は機種ごとに1回でよく全サイズで使い回せる。
# 指示書 1.1・3.2 参照）なので、campaign の --size には連動させない。
DEFAULT_MAT_SIZE = 4096
DEFAULT_N_IT = N_IT_DEFAULT  # 50
DEFAULT_IB_STEP = 2


def flops_dtsmqr(nb: int, ib: int) -> float:
    """暫定式。flops_ssrfb（4*nb^3）を再利用（要検証）。"""
    return flops_ssrfb(nb, ib)


def run_dtsmqr_point(
    nb: int,
    ib: int,
    threads: int = 1,
    exe: str = None,
    mat_size: int = DEFAULT_MAT_SIZE,
    n_it: int = DEFAULT_N_IT,
):
    """(nb, ib) 単体の平均実行時間（秒）を1プロセス起動で計測する。

    n_it は NoFlush 内部の平均化回数（--kernel-reps/--beta-reps がここに渡る。
    モジュール冒頭の理由を参照）。失敗時は None。
    """
    t = measure_time(nb, ib, exe=exe, threads=threads, mat_size=mat_size, n_it=n_it)
    return None if t == float("inf") else t


def run_dtsmqr_sweep(
    nb: int,
    ib_lo: int,
    ib_hi: int,
    ib_step: int = DEFAULT_IB_STEP,
    threads: int = 1,
    exe: str = None,
    mat_size: int = DEFAULT_MAT_SIZE,
    n_it: int = DEFAULT_N_IT,
):
    """nb を固定して ib_lo〜ib_hi を ib_step 刻みで計測する（1点=1プロセス起動）。

    Returns
    -------
    (times, n_launches)
        times: {ib: time_sec}（失敗した ib は含まれない）
        n_launches: 実際に起動したプロセス数（時間内訳検証・n_kernel_exec 用）
    """
    times = {}
    n_launches = 0
    ib = ib_lo
    while ib <= ib_hi:
        n_launches += 1
        t = run_dtsmqr_point(nb, ib, threads=threads, exe=exe, mat_size=mat_size, n_it=n_it)
        if t is not None:
            times[ib] = t
        ib += ib_step

    if not times:
        print(
            f"[WARN] dtsmqr 計測失敗またはゼロ点: nb={nb}, ib=[{ib_lo},{ib_hi}], "
            f"threads={threads}"
        )

    return times, n_launches
