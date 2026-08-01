"""coverage.py — results/ を走査して各行の充足状況を計算する。

- bench（ssrfb/tileqr）: CSV を読み、欠測でない (nb,ib) 点集合を
  (kind, size, threads) ごとに作る。ファイル名の host 接頭辞でこのマシン分に絞る。
- tuning: results/optuna/<mode>/s<size>_th<threads>/<n_trials>/optuna_results.csv の
  データ行数 = 実行済み回数。
"""
from __future__ import annotations

import csv
import glob
import os
import socket
from typing import Dict, Optional, Set, Tuple

from ..core.sweep import count_points
from .manifest import BenchRow, Manifest, TuneRow

Point = Tuple[int, int]
BenchIndex = Dict[Tuple[str, int, int], Set[Point]]


def _kind_of(basename: str) -> str:
    return "ssrfb" if "_ssrfb_" in basename else "tileqr"


def scan_bench_points(bench_results_dir: str, host: Optional[str] = None) -> BenchIndex:
    """(kind, size, threads) -> 欠測でない (nb,ib) の集合。"""
    host = host or socket.gethostname()
    index: BenchIndex = {}
    for path in glob.glob(os.path.join(bench_results_dir, "*.csv")):
        base = os.path.basename(path)
        if not base.startswith(host + "_"):
            continue
        kind = _kind_of(base)
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    try:
                        size = int(r["size"]); threads = int(r["threads"])
                        nb = int(r["nb"]); ib = int(r["ib"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    if not _is_valid(kind, r):
                        continue
                    index.setdefault((kind, size, threads), set()).add((nb, ib))
        except OSError:
            continue
    return index


def _is_valid(kind: str, row: dict) -> bool:
    try:
        if kind == "ssrfb":
            return float(row.get("Time_sec", "nan")) > 0
        return float(row.get("GFlops", "nan")) > 0
    except (ValueError, TypeError):
        return False


def _grid(row: BenchRow):
    """対象の (nb, ib) グリッドを nb 昇順で列挙する。"""
    for nb in range(row.nb_min, row.nb_max + 1, row.step):
        for ib in range(row.ib_min, nb // 2 + 1, row.step):
            yield nb, ib


def bench_status(row: BenchRow, index: BenchIndex) -> Optional[int]:
    """row.status / row.coverage を更新し、再開すべき nb（None=完了）を返す。"""
    covered = index.get((row.kind, row.size, row.threads), set())
    total = count_points(row.nb_min, row.nb_max, row.ib_min, row.step)

    got = 0
    resume_nb = None
    for nb in range(row.nb_min, row.nb_max + 1, row.step):
        nb_complete = True
        for ib in range(row.ib_min, nb // 2 + 1, row.step):
            if (nb, ib) in covered:
                got += 1
            else:
                nb_complete = False
        if not nb_complete and resume_nb is None:
            resume_nb = nb

    row.coverage = f"{got}/{total}"
    if got == 0:
        row.status = "missing"
    elif got >= total:
        row.status = "done"
        resume_nb = None
    else:
        row.status = "partial"
    return resume_nb


def scan_tune_counts(optuna_dir: str) -> Dict[Tuple[str, int, int, int], int]:
    """(mode, size, threads, n_trials) -> 実行済み回数（optuna_results.csv の行数）。"""
    index: Dict[Tuple[str, int, int, int], int] = {}
    pattern = os.path.join(optuna_dir, "*", "s*_th*", "*", "optuna_results.csv")
    for path in glob.glob(pattern):
        parts = path.split(os.sep)
        try:
            mode = parts[-4]
            st = parts[-3]                       # s{size}_th{threads}
            n_trials = int(parts[-2])
            size = int(st.split("_")[0][1:])
            threads = int(st.split("_")[1][2:])
        except (IndexError, ValueError):
            continue
        try:
            with open(path, newline="") as f:
                n = sum(1 for _ in csv.reader(f))
            done = max(0, n - 1)                 # ヘッダ1行を除く
        except OSError:
            continue
        index[(mode, size, threads, n_trials)] = done
    return index


def tune_status(row: TuneRow, index: Dict[Tuple[str, int, int, int], int]) -> int:
    """row.status / row.coverage を更新し、残り実行回数を返す。"""
    done = index.get((row.mode, row.size, row.threads, row.n_trials), 0)
    done = min(done, row.counts)
    row.coverage = f"{done}/{row.counts}"
    if done == 0:
        row.status = "missing"
    elif done >= row.counts:
        row.status = "done"
    else:
        row.status = "partial"
    return max(0, row.counts - done)


def refresh(manifest: Manifest, bench_results_dir: str, optuna_dir: str,
            host: Optional[str] = None) -> None:
    """マニフェスト全行の status/coverage を results/ から更新する。"""
    bindex = scan_bench_points(bench_results_dir, host=host)
    for row in manifest.ssrfb + manifest.tileqr:
        bench_status(row, bindex)
    tindex = scan_tune_counts(optuna_dir)
    for row in manifest.tuning:
        tune_status(row, tindex)
