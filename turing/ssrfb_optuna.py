"""
ssrfb_optuna.py — NoFlush で最適 ib を探索してから GFLOPS を計測する Optuna チューニング

NoFlush のパスは環境変数 NOFLUSH_PATH で指定する（setup/config.sh で設定）。
"""
import optuna
import subprocess
import time
import csv
import os

from core.run_time_dgeqrf import run_time_dgeqrf

optuna.logging.set_verbosity(optuna.logging.WARNING)

_NOFLUSH_PATH = os.environ.get(
    "NOFLUSH_PATH",
    os.path.expanduser("~/Library/Tune_SSRFB/NoFlush"),
)


def find_best_ib(nb: int, ib_min: int, ib_max: int, tmp_csv: str) -> tuple:
    """
    NoFlush を使って nb に対する最適な ib を探索する。

    Returns
    -------
    (best_ib, best_time) : (int | None, float)
    """
    best_ib = None
    best_time = float("inf")
    t_start = time.time()

    for ib in range(ib_min, ib_max + 1, 2):
        print(f"  Testing nb={nb}, ib={ib}...")
        try:
            result = subprocess.run(
                [_NOFLUSH_PATH, str(nb), str(ib), str(ib)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            for line in result.stdout.splitlines():
                if line.startswith(f"{nb}, {ib},"):
                    _, _, t_str = line.split(", ")
                    t_val = float(t_str)
                    if t_val < best_time:
                        best_time = t_val
                        best_ib = ib
        except subprocess.CalledProcessError as e:
            print(f"  [WARN] NoFlush 失敗: nb={nb}, ib={ib}: {e}")
            continue

    elapsed = time.time() - t_start

    # ログをCSVに保存
    os.makedirs(os.path.dirname(tmp_csv), exist_ok=True)
    write_header = not os.path.exists(tmp_csv)
    with open(tmp_csv, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["nb", "ib_min", "ib_max", "best_ib", "execution_time"])
        writer.writerow([nb, ib_min, ib_max, best_ib, f"{elapsed:.4f}"])

    return best_ib, best_time


def make_objective(size: int, nb_min: int, nb_max: int, num_threads: int, tmp_csv: str):
    def objective(trial: optuna.Trial) -> float:
        nb = 2 * trial.suggest_int("nb", nb_min // 2, nb_max // 2)
        best_ib, _ = find_best_ib(nb, 4, nb // 2, tmp_csv)

        if best_ib is None:
            print(f"  [WARN] nb={nb} に有効な ib が見つかりませんでした")
            return 0.0

        print(f"  nb={nb}, best_ib={best_ib}")
        return run_time_dgeqrf(num_threads, size, nb, best_ib)
    return objective


def ssrfb_tuning(
    n_trials: int,
    size: int = 4096,
    nb_min: int = 192,
    nb_max: int = 336,
    num_threads: int = None,
    tmp_csv: str = None,
):
    if num_threads is None:
        num_threads = os.cpu_count()
    if tmp_csv is None:
        tmp_csv = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "results", "optuna", "ssrfb", "tmp", "find_best_ib.csv"
        )

    start = time.time()
    study = optuna.create_study(direction="maximize")
    study.optimize(
        make_objective(size, nb_min, nb_max, num_threads, tmp_csv),
        n_trials=n_trials,
    )
    return study, time.time() - start


def main():
    study, elapsed = ssrfb_tuning(n_trials=20)
    print(study.best_trial)
    print(f"処理にかかった時間は {elapsed:.1f} 秒です")


if __name__ == "__main__":
    main()
