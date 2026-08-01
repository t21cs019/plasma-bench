"""paths.py — リポジトリ内の標準パス定義。

`python -m plasma_perf ...` を `src/` を PYTHONPATH に載せて実行する前提で、
このファイル（src/plasma_perf/core/paths.py）から3階層上がリポジトリルート。
"""
import os

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
RESULTS_DIR = os.path.join(REPO_ROOT, "results")
BENCH_RESULTS_DIR = os.path.join(RESULTS_DIR, "benchmark")
OPTUNA_RESULTS_DIR = os.path.join(RESULTS_DIR, "optuna")
LOGS_DIR = os.path.join(REPO_ROOT, "logs")
PLAN_DIR = os.path.join(REPO_ROOT, "plan")
PROGRESS_MD = os.path.join(PLAN_DIR, "PROGRESS.md")
