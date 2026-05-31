"""
select_optuna.py — nb の探索範囲を絞った Optuna チューニング

normalで良好な結果が出た範囲を絞り込んで精密に探索する。
nb_min・nb_max はマシンごとに指定する。
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


def select_tuning(
    n_trials: int,
    size: int = 4096,
    nb_min: int = 192,
    nb_max: int = 336,
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
    study, elapsed = select_tuning(n_trials=20)
    print(study.best_trial)
    print(f"処理にかかった時間は {elapsed:.1f} 秒です")


if __name__ == "__main__":
    main()
