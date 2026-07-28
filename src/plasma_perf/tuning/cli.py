"""cli.py — `python -m plasma_perf tune {normal,select,ssrfb,all}` のエントリポイント。

例:
    python -m plasma_perf tune normal --counts 5 --trials 25
    python -m plasma_perf tune select --nb-min 192 --nb-max 336 --counts 10 --trials 50
    python -m plasma_perf tune all    --counts 10 --trials 25 50 100
"""
from __future__ import annotations

import argparse
import os
from typing import List, Optional

from ..core.paths import OPTUNA_RESULTS_DIR
from .base import save_best_trial, visualize_study_results


def _best_params_with_ib(trial) -> dict:
    """best_params に ib が無い場合（ssrfb）は user_attr から補う。"""
    params = dict(trial.params)
    if "ib" not in params:
        ib = trial.user_attrs.get("ib")
        if ib is not None:
            params["ib"] = ib
    return params


def optuna_run_dir(mode: str, size: int, threads: int, n_trials: int) -> str:
    """結果ディレクトリ: results/optuna/<mode>/s<size>_th<threads>/<n_trials>/。

    size/threads をパスに含めることで、plan の coverage が
    (mode, size, threads, n_trials) 単位で充足状況を判定できる。
    """
    return os.path.join(OPTUNA_RESULTS_DIR, mode, f"s{size}_th{threads}", str(n_trials))


def optuna_csv_path(mode: str, size: int, threads: int, n_trials: int) -> str:
    return os.path.join(optuna_run_dir(mode, size, threads, n_trials), "optuna_results.csv")


def run_mode(mode: str, tuning_fn, counts: int, trials_list: List[int], extra_kwargs: dict) -> None:
    size = extra_kwargs.get("size", 4096)
    threads = extra_kwargs.get("num_threads") or 0

    for n_trials in trials_list:
        run_dir = optuna_run_dir(mode, size, threads, n_trials)
        csv_path = os.path.join(run_dir, "optuna_results.csv")

        for i in range(counts):
            fig_dir = os.path.join(run_dir, f"run_{i + 1}")
            os.makedirs(fig_dir, exist_ok=True)

            print(f"\n=== [{mode}] {i + 1}/{counts} 回目 (trials={n_trials}) ===")
            study, elapsed = tuning_fn(n_trials=n_trials, **extra_kwargs)

            best_params = _best_params_with_ib(study.best_trial)
            best_value = study.best_trial.value

            save_best_trial(i + 1, elapsed, best_params, best_value, csv_path)
            try:
                visualize_study_results(best_params, best_value, study, fig_dir)
            except Exception as e:  # 可視化依存（plotly/kaleido）が無くても計測は継続
                print(f"  [WARN] 可視化をスキップ: {e}")

            print(f"  最適: nb={best_params.get('nb')}, ib={best_params.get('ib')}, "
                  f"GFLOPS={best_value:.4f}, {elapsed:.1f}秒")

    print(f"\n[{mode}] 完了 → {os.path.join(OPTUNA_RESULTS_DIR, mode)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plasma_perf tune",
        description="PLASMA dgeqrf Optuna チューニング",
    )
    parser.add_argument("mode", choices=["normal", "select", "ssrfb", "all"])
    parser.add_argument("--counts", type=int, default=10)
    parser.add_argument("--trials", type=int, nargs="+", default=[25])
    parser.add_argument("--size", type=int, default=4096)
    parser.add_argument("--nb-min", type=int, default=None)
    parser.add_argument("--nb-max", type=int, default=None)
    parser.add_argument("--threads", type=int, default=os.cpu_count())
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # 先に引数を解釈（--help はここで完結し、optuna 不在でも動く）。
    args = build_parser().parse_args(argv)

    # optuna は optional 依存。実際に走らせる段になってから import する。
    from .normal_optuna import normal_tuning
    from .select_optuna import select_tuning
    from .ssrfb_optuna import ssrfb_tuning

    common = {"size": args.size, "num_threads": args.threads}
    if args.nb_min:
        common["nb_min"] = args.nb_min
    if args.nb_max:
        common["nb_max"] = args.nb_max

    modes = {
        "normal": normal_tuning,
        "select": select_tuning,
        "ssrfb": ssrfb_tuning,
    }
    targets = list(modes.keys()) if args.mode == "all" else [args.mode]

    for mode in targets:
        run_mode(mode, modes[mode], args.counts, args.trials, common)

    print("\n全ての最適化が完了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
