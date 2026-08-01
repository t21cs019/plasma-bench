"""cli.py — `python -m plasma_perf plan {init,scan,add,run}`。

    plan init                    PROGRESS.md の雛形を作る
    plan scan                    results/ を走査して status/coverage を更新
    plan add {ssrfb,tileqr,tuning} ...   直積展開で行を追加
    plan run [--dry-run] ...     未計測（missing/partial）を実行
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from ..core import paths
from ..core.paths import PROGRESS_MD
from . import coverage, manifest
from .manifest import BenchRow, TuneRow
from .runner import run_plan


def _ints(text: str) -> List[int]:
    return [int(x) for x in text.split(",") if x.strip()]


def _print_summary(m: manifest.Manifest) -> None:
    def counts(rows):
        c = {"done": 0, "partial": 0, "missing": 0}
        for r in rows:
            c[r.status] = c.get(r.status, 0) + 1
        return c
    for name, rows in (("ssrfb", m.ssrfb), ("tileqr", m.tileqr), ("tuning", m.tuning)):
        c = counts(rows)
        print(f"  {name:7s}: {len(rows)} 行  "
              f"done={c['done']} partial={c['partial']} missing={c['missing']}")


def _cmd_init() -> int:
    import os
    if os.path.exists(PROGRESS_MD):
        print(f"[plan] 既に存在します: {PROGRESS_MD}")
        return 0
    manifest.save(PROGRESS_MD, manifest.Manifest())
    print(f"[plan] 雛形を作成しました: {PROGRESS_MD}")
    print("      `plan add ...` で行を追加してください。")
    return 0


def _cmd_scan() -> int:
    m = manifest.load(PROGRESS_MD)
    coverage.refresh(m, paths.BENCH_RESULTS_DIR, paths.OPTUNA_RESULTS_DIR)
    manifest.save(PROGRESS_MD, m)
    print(f"[plan] 走査して更新しました: {PROGRESS_MD}")
    _print_summary(m)
    return 0


def _cmd_add(args) -> int:
    m = manifest.load(PROGRESS_MD)
    nb_min, nb_max = manifest._parse_nb(args.nb)
    sizes = _ints(args.sizes)
    threads = _ints(args.threads)
    added = 0

    if args.target == "tuning":
        if not args.mode:
            print("[ERROR] tuning には --mode が必要です（normal/select/ssrfb）")
            return 2
        existing = {r.key() for r in m.tuning}
        for size in sizes:
            for th in threads:
                for ntr in _ints(args.n_trials):
                    row = TuneRow(mode=args.mode, size=size, threads=th,
                                  n_trials=ntr, counts=args.counts)
                    if row.key() not in existing:
                        m.tuning.append(row)
                        existing.add(row.key())
                        added += 1
    else:
        existing = {r.key() for r in (m.ssrfb if args.target == "ssrfb" else m.tileqr)}
        n_its = _ints(args.n_it) if args.target == "ssrfb" else [None]
        for size in sizes:
            for th in threads:
                for nit in n_its:
                    row = BenchRow(
                        kind=args.target, size=size, threads=th,
                        nb_min=nb_min, nb_max=nb_max, ib_min=args.ib_min,
                        step=args.step, trials=args.trials, n_it=nit,
                    )
                    if row.key() not in existing:
                        (m.ssrfb if args.target == "ssrfb" else m.tileqr).append(row)
                        existing.add(row.key())
                        added += 1

    coverage.refresh(m, paths.BENCH_RESULTS_DIR, paths.OPTUNA_RESULTS_DIR)
    manifest.save(PROGRESS_MD, m)
    print(f"[plan] {args.target} に {added} 行を追加しました → {PROGRESS_MD}")
    _print_summary(m)
    return 0


def _cmd_run(args) -> int:
    run_plan(
        manifest_path=PROGRESS_MD,
        exe_ssrfb=args.exe_ssrfb, exe_tileqr=args.exe_tileqr,
        only=args.only, throttle_check=args.throttle_check,
        throttle_abort=args.throttle_abort, wrapper=args.wrapper,
        assumed_gflops=args.assumed_gflops, timeout_sec=args.timeout_sec,
        save_logs=args.save_logs, dry_run=args.dry_run,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plasma_perf plan",
                                     description="計測進捗の管理と未計測分の自動実行")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="PROGRESS.md の雛形を作る")
    sub.add_parser("scan", help="results/ を走査して status/coverage を更新")

    p_add = sub.add_parser("add", help="行を追加（直積展開）")
    p_add.add_argument("target", choices=["ssrfb", "tileqr", "tuning"])
    p_add.add_argument("--sizes", required=True, help="カンマ区切り（例 1024,2048,4096）")
    p_add.add_argument("--threads", required=True, help="カンマ区切り（例 1,12）")
    p_add.add_argument("--nb", default="32-512", help="nb 範囲 min-max（既定 32-512）")
    p_add.add_argument("--ib-min", type=int, default=8)
    p_add.add_argument("--step", type=int, default=4)
    p_add.add_argument("--trials", type=int, default=5)
    p_add.add_argument("--n-it", default="50", help="ssrfb: N_IT（カンマ区切り可。既定 50）")
    p_add.add_argument("--mode", choices=["normal", "select", "ssrfb"],
                       help="tuning: 探索モード")
    p_add.add_argument("--n-trials", default="25", help="tuning: n_trials（カンマ区切り可）")
    p_add.add_argument("--counts", type=int, default=10, help="tuning: 反復回数")

    p_run = sub.add_parser("run", help="未計測（missing/partial）を実行")
    p_run.add_argument("--exe-ssrfb", default=None, help="NoFlush パス（既定 環境変数 NOFLUSH_PATH）")
    p_run.add_argument("--exe-tileqr", default=None, help="plasmatest パス（既定 環境変数 PLASMA_TEST）")
    p_run.add_argument("--only", nargs="*", choices=["ssrfb", "tileqr", "tuning"],
                       default=None, help="対象種別を限定")
    p_run.add_argument("--throttle-check", action="store_true")
    p_run.add_argument("--throttle-abort", action="store_true")
    p_run.add_argument("--wrapper", default=None)
    p_run.add_argument("--assumed-gflops", type=float, default=5.0)
    p_run.add_argument("--timeout-sec", type=float, default=None)
    p_run.add_argument("--save-logs", choices=["all", "errors", "none"], default="errors")
    p_run.add_argument("--dry-run", action="store_true", help="実行せず対象一覧を表示")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        return _cmd_init()
    if args.cmd == "scan":
        return _cmd_scan()
    if args.cmd == "add":
        return _cmd_add(args)
    if args.cmd == "run":
        return _cmd_run(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
