"""
normal_optuna.py — nb・ib をベイズ最適化で探索する（広い探索範囲）

デフォルト探索範囲:
  nb: 20 ～ 512（偶数）
  ib: 4 ～ nb//2（偶数）
"""
import optuna
import time
import os

from core.run_time_dgeqrf import run_time_dgeqrf

optuna.logging.set_verbosity(optuna.logging.WARNING)


def make_objective(size: int, nb_min: int, nb_max: int, num_threads: int):
    def objective(trial: optuna.Trial) -> float:
        nb = 2 * trial.suggest_int("nb", nb_min // 2, nb_max // 2)
        ib = 2 * trial.suggest_int("ib", 2, nb // 4)
        return run_time_dgeqrf(num_threads, size, nb, ib)
    return objective


def normal_tuning(
    n_trials: int,
    size: int = 4096,
    nb_min: int = 20,
    nb_max: int = 512,
    num_threads: int = None,
):
    """
    Returns
    -------
    study   : optuna.Study
    elapsed : float  実行時間（秒）
    """
    if num_threads is None:
        num_threads = os.cpu_count()

    start = time.time()
    study = optuna.create_study(direction="maximize")
    study.optimize(make_objective(size, nb_min, nb_max, num_threads), n_trials=n_trials)
    return study, time.time() - start


def main():
    study, elapsed = normal_tuning(n_trials=20)
    print(study.best_trial)
    print(f"処理にかかった時間は {elapsed:.1f} 秒です")


if __name__ == "__main__":
    main()
