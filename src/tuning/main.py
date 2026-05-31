"""
main.py — Optuna チューニングのメイン実行スクリプト

使い方:
    python -m tuning.main [オプション]

例:
    python -m tuning.main --mode normal --counts 5 --trials 25
    python -m tuning.main --mode select --nb-min 192 --nb-max 336 --counts 10 --trials 50
    python -m tuning.main --mode all --counts 10 --trials 25 50 100
"""
import argparse
import os

from tuning.base import save_best_trial, visualize_study_results
from tuning.normal_optuna import normal_tuning
from tuning.select_optuna import select_tuning
from tuning.ssrfb_optuna import ssrfb_tuning

RESULTS_BASE = os.path.join(os.path.dirname(__file__), "..", "..", "results", "optuna")


def run_mode(mode: str, tuning_fn, counts: int, trials_list: list, extra_kwargs: dict):
    result_dir = os.path.join(RESULTS_BASE, mode)

    for n_trials in trials_list:
        csv_path = os.path.join(result_dir, str(n_trials), "optuna_results.csv")

        for i in range(counts):
            fig_dir = os.path.join(result_dir, str(n_trials), f"run_{i + 1}")
            os.makedirs(fig_dir, exist_ok=True)

            print(f"\n=== [{mode}] {i + 1}/{counts} 回目 (trials={n_trials}) ===")
            study, elapsed = tuning_fn(n_trials=n_trials, **extra_kwargs)

            best_params = study.best_trial.params
            best_value = study.best_trial.value

            save_best_trial(i + 1, elapsed, best_params, best_value, csv_path)
            visualize_study_results(best_params, best_value, study, fig_dir)

            print(f"  最適: nb={best_params['nb']}, ib={best_params['ib']}, "
                  f"GFLOPS={best_value:.4f}, {elapsed:.1f}秒")

    print(f"\n[{mode}] 完了 → {result_dir}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="PLASMA dgeqrf Optuna チューニング",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["normal", "select", "ssrfb", "all"],
        default="normal",
    )
    parser.add_argument("--counts",  type=int, default=10)
    parser.add_argument("--trials",  type=int, nargs="+", default=[25])
    parser.add_argument("--size",    type=int, default=4096)
    parser.add_argument("--nb-min",  type=int, default=None)
    parser.add_argument("--nb-max",  type=int, default=None)
    parser.add_argument("--threads", type=int, default=os.cpu_count())
    return parser.parse_args()


def main():
    args = parse_args()

    common = {"size": args.size, "num_threads": args.threads}
    if args.nb_min:
        common["nb_min"] = args.nb_min
    if args.nb_max:
        common["nb_max"] = args.nb_max

    modes = {
        "normal": normal_tuning,
        "select": select_tuning,
        "ssrfb":  ssrfb_tuning,
    }

    targets = list(modes.keys()) if args.mode == "all" else [args.mode]

    for mode in targets:
        run_mode(mode, modes[mode], args.counts, args.trials, common)

    print("\n全ての最適化が完了しました。")


if __name__ == "__main__":
    main()
