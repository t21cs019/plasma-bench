"""ssrfb_optuna.py — NoFlush で最適 ib を探索してから dgeqrf の GFLOPS を計測する Optuna チューニング。

各 trial で nb を提案し、その nb に対する最良 ib を NoFlush の実測（カーネル時間最小）で
決めてから、その (nb, ib) の dgeqrf GFLOPS を目的関数値とする。

NoFlush のパスは --exe か環境変数 NOFLUSH_PATH、plasmatest は環境変数 PLASMA_TEST。
"""
import os
import time

import optuna

from ..benchmarks.ssrfb.runner import measure_time
from ..benchmarks.tileqr.runner import measure_gflops

optuna.logging.set_verbosity(optuna.logging.WARNING)


def find_best_ib(nb: int, ib_min: int, ib_max: int, exe: str = None, step: int = 2):
    """NoFlush の実測カーネル時間が最小になる ib を探す。

    Returns
    -------
    (best_ib, best_time) : (int | None, float)
    """
    best_ib = None
    best_time = float("inf")
    for ib in range(ib_min, ib_max + 1, step):
        t = measure_time(nb, ib, exe=exe)
        if t < best_time:
            best_time = t
            best_ib = ib
    return best_ib, best_time


def make_objective(size: int, nb_min: int, nb_max: int, num_threads: int, noflush_exe: str = None):
    def objective(trial: "optuna.Trial") -> float:
        nb = 2 * trial.suggest_int("nb", nb_min // 2, nb_max // 2)
        best_ib, _ = find_best_ib(nb, 4, nb // 2, exe=noflush_exe)

        if best_ib is None:
            print(f"  [WARN] nb={nb} に有効な ib が見つかりませんでした")
            trial.set_user_attr("ib", None)
            return 0.0

        trial.set_user_attr("ib", best_ib)
        print(f"  nb={nb}, best_ib={best_ib}")
        return measure_gflops(nb, best_ib, threads=num_threads, size=size)
    return objective


def ssrfb_tuning(
    n_trials: int,
    size: int = 4096,
    nb_min: int = 192,
    nb_max: int = 336,
    num_threads: int = None,
    noflush_exe: str = None,
):
    if num_threads is None:
        num_threads = os.cpu_count()

    start = time.time()
    study = optuna.create_study(direction="maximize")
    study.optimize(
        make_objective(size, nb_min, nb_max, num_threads, noflush_exe),
        n_trials=n_trials,
    )
    return study, time.time() - start


def main():
    study, elapsed = ssrfb_tuning(n_trials=20)
    print(study.best_trial)
    print(f"処理にかかった時間は {elapsed:.1f} 秒です")


if __name__ == "__main__":
    main()
