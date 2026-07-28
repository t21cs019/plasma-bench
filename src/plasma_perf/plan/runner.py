"""runner.py — 進捗ファイルを見て未計測（missing/partial）を実行する。

1回の `plan run` で、実行対象の行を上から順にすべて処理する（＝溜まった分を回し切る）。
partial は未充足 nb から再開する。実行後に再スキャンして PROGRESS.md を更新する。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from ..core import paths
from . import coverage, manifest
from .manifest import BenchRow, Manifest, TuneRow


def _run_bench(
    row: BenchRow,
    resume_nb: Optional[int],
    *,
    exe: Optional[str],
    throttle_check: bool,
    throttle_abort: bool,
    wrapper: Optional[str],
    assumed_gflops: float,
    timeout_sec: Optional[float],
    save_logs: str,
) -> None:
    from ..benchmarks.ssrfb.runner import SSRFBBenchmark, N_IT_DEFAULT
    from ..benchmarks.tileqr.runner import TileQRBenchmark
    from ..core.csv_io import build_output_path
    from ..core.sweep import run_sweep

    if row.kind == "ssrfb":
        bench = SSRFBBenchmark(exe=exe, n_it=row.n_it or N_IT_DEFAULT)
    else:
        bench = TileQRBenchmark(exe=exe)

    exe_path = os.path.expanduser(bench.exe)
    if not os.path.isfile(exe_path):
        print(f"[skip] 実行ファイルが見つかりません: {exe_path}"
              f" → {row.kind} size={row.size} th={row.threads} をスキップ")
        return

    nb_start = resume_nb if resume_nb is not None else row.nb_min
    print(f"\n>>> {row.kind} size={row.size} th={row.threads} "
          f"nb={nb_start}..{row.nb_max}（resume）" if resume_nb and resume_nb != row.nb_min
          else f"\n>>> {row.kind} size={row.size} th={row.threads} nb={nb_start}..{row.nb_max}")

    output_csv = build_output_path(
        paths.BENCH_RESULTS_DIR, size=row.size, nb_min=row.nb_min, nb_max=row.nb_max,
        trials=row.trials, tag=bench.tag or None, threads=row.threads,
    )
    log_dir = os.path.join(paths.LOGS_DIR, f"raw_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    run_sweep(
        bench, threads=row.threads, size=row.size,
        nb_min=nb_start, nb_max=row.nb_max, ib_min=row.ib_min, step=row.step,
        trials=row.trials, output_csv=output_csv, log_dir=log_dir,
        timeout_sec=timeout_sec, assumed_gflops=assumed_gflops,
        wrapper=wrapper, save_logs=save_logs,
        throttle_check=throttle_check, throttle_abort=throttle_abort,
    )


def _run_tune(row: TuneRow, remaining: int) -> None:
    try:
        import optuna  # noqa: F401
    except ImportError:
        print("[skip] optuna 未インストール（uv: bash scripts/setup_python.sh）"
              f" → tuning {row.mode} をスキップ")
        return

    from ..tuning.base import save_best_trial
    from ..tuning.cli import _best_params_with_ib, optuna_csv_path
    from ..tuning.normal_optuna import normal_tuning
    from ..tuning.select_optuna import select_tuning
    from ..tuning.ssrfb_optuna import ssrfb_tuning

    fns = {"normal": normal_tuning, "select": select_tuning, "ssrfb": ssrfb_tuning}
    fn = fns.get(row.mode)
    if fn is None:
        print(f"[skip] 未知の tuning mode: {row.mode}")
        return

    csv_path = optuna_csv_path(row.mode, row.size, row.threads, row.n_trials)
    done = row.counts - remaining
    print(f"\n>>> tune {row.mode} size={row.size} th={row.threads} "
          f"trials={row.n_trials} 残り{remaining}回")
    for i in range(remaining):
        study, elapsed = fn(n_trials=row.n_trials, size=row.size, num_threads=row.threads)
        best_params = _best_params_with_ib(study.best_trial)
        save_best_trial(done + i + 1, elapsed, best_params, study.best_trial.value, csv_path)
        print(f"  [{done + i + 1}/{row.counts}] nb={best_params.get('nb')}, "
              f"ib={best_params.get('ib')}, obj={study.best_trial.value:.4f}")


def run_plan(
    *,
    manifest_path: str,
    exe_ssrfb: Optional[str] = None,
    exe_tileqr: Optional[str] = None,
    only: Optional[List[str]] = None,
    throttle_check: bool = False,
    throttle_abort: bool = False,
    wrapper: Optional[str] = None,
    assumed_gflops: float = 5.0,
    timeout_sec: Optional[float] = None,
    save_logs: str = "errors",
    dry_run: bool = False,
) -> int:
    """未計測分を実行する。処理した行数を返す。"""
    m = manifest.load(manifest_path)
    if not (m.ssrfb or m.tileqr or m.tuning):
        print(f"[plan] 対象がありません。まず `plan add ...` で行を追加してください（{manifest_path}）")
        return 0

    coverage.refresh(m, paths.BENCH_RESULTS_DIR, paths.OPTUNA_RESULTS_DIR)
    manifest.save(manifest_path, m)

    targets = [r for r in m.actionable() if not only or _kind(r) in only]
    if not targets:
        print("[plan] 実行対象（missing/partial）はありません。すべて done か skip/manual です。")
        return 0

    if dry_run:
        print("[plan] dry-run: 以下を実行します（実際には走らせません）:")
        for r in targets:
            print(f"  - {_describe(r)}")
        return 0

    bindex = coverage.scan_bench_points(paths.BENCH_RESULTS_DIR)
    tindex = coverage.scan_tune_counts(paths.OPTUNA_RESULTS_DIR)
    processed = 0
    for row in targets:
        if isinstance(row, BenchRow):
            resume_nb = coverage.bench_status(row, bindex)
            exe = exe_ssrfb if row.kind == "ssrfb" else exe_tileqr
            _run_bench(
                row, resume_nb, exe=exe,
                throttle_check=throttle_check, throttle_abort=throttle_abort,
                wrapper=wrapper, assumed_gflops=assumed_gflops,
                timeout_sec=timeout_sec, save_logs=save_logs,
            )
        else:
            remaining = coverage.tune_status(row, tindex)
            _run_tune(row, remaining)
        processed += 1
        # 実行のたびに再スキャンして進捗ファイルを更新
        coverage.refresh(m, paths.BENCH_RESULTS_DIR, paths.OPTUNA_RESULTS_DIR)
        manifest.save(manifest_path, m)

    print(f"\n[plan] {processed} 行を処理しました。進捗: {manifest_path}")
    return processed


def _kind(row) -> str:
    return row.kind if isinstance(row, BenchRow) else "tuning"


def _describe(row) -> str:
    if isinstance(row, BenchRow):
        return (f"{row.kind} size={row.size} th={row.threads} "
                f"nb={row.nb} trials={row.trials} [{row.status} {row.coverage}]")
    return (f"tuning {row.mode} size={row.size} th={row.threads} "
            f"trials={row.n_trials} counts={row.counts} [{row.status} {row.coverage}]")
