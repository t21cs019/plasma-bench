"""
optuna_full.py — 卒論の Optuna（広域探索）をそのまま再現する。

nb: 4〜512 の偶数、ib: 2〜nb/2 の偶数。100試行、1試行につき QR を5回計測して
その平均を目的値とする（卒論通り）。探索時間のみを計測する（前処理なし）。
"""
import os
import sys
import time

try:
    import optuna
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "optuna_full には optuna が必要です。"
        "`bash scripts/setup_python.sh` を実行し、"
        "`uv run python tuning/run_tuning.py ...` で起動してください。"
    ) from e

optuna.logging.set_verbosity(optuna.logging.WARNING)

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
from plasma_perf.benchmarks.tileqr.runner import measure_gflops  # noqa: E402

NB_MIN = 4
NB_MAX = 512
N_TRIALS = 100
QR_REPS = 5


def run(size, threads, seed, ctx=None, **kwargs):
    """
    kwargs:
        n_trials : 試行数（既定100、卒論通り）
        qr_reps  : 1試行あたりの QR 計測回数（既定5、卒論通り）
        nb_min, nb_max : nb 探索範囲（既定4,512、卒論通り）

    Returns
    -------
    dict: nb, ib, time_total_sec, time_prep_sec(=0), n_qr_exec, n_kernel_exec(=0),
          beta(=None), band_lo(=None), band_hi(=None), gflops_observed, notes
    """
    n_trials = kwargs.get("n_trials", N_TRIALS)
    qr_reps = kwargs.get("qr_reps", QR_REPS)
    nb_min = kwargs.get("nb_min", NB_MIN)
    nb_max = kwargs.get("nb_max", NB_MAX)

    n_qr_exec = 0

    def objective(trial: "optuna.Trial") -> float:
        nonlocal n_qr_exec
        nb = 2 * trial.suggest_int("nb_half", nb_min // 2, nb_max // 2)
        ib_half_max = max(1, nb // 4)
        ib = 2 * trial.suggest_int("ib_half", 1, ib_half_max)
        vals = []
        for _ in range(qr_reps):
            vals.append(measure_gflops(nb, ib, threads=threads, size=size))
            n_qr_exec += 1
        return sum(vals) / len(vals)

    t0 = time.time()
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=seed),
    )
    study.optimize(objective, n_trials=n_trials)
    elapsed = time.time() - t0

    best = study.best_trial
    nb = 2 * best.params["nb_half"]
    ib = 2 * best.params["ib_half"]

    notes = ""
    if nb_min != NB_MIN or nb_max != NB_MAX:
        notes = (
            f"DEVIATION from thesis: nb range overridden to [{nb_min},{nb_max}] "
            f"(thesis default [{NB_MIN},{NB_MAX}]). Small nb makes PLASMA's task "
            f"count blow up as (size/nb)^3, which OOM'd this host at nb=4."
        )

    return {
        "nb": nb,
        "ib": ib,
        "time_total_sec": elapsed,
        "time_prep_sec": 0.0,
        "n_qr_exec": n_qr_exec,
        "n_kernel_exec": 0,
        "beta": None,
        "band_lo": None,
        "band_hi": None,
        "gflops_observed": best.value,
        "notes": notes,
    }
