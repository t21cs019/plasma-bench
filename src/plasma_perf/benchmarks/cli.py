"""cli.py — `python -m plasma_perf bench {tileqr,ssrfb}` のエントリポイント。

共通のスイープ引数を1つの parser にまとめ、target で benchmark を切り替える。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional

from ..core.csv_io import build_output_path
from ..core.paths import BENCH_RESULTS_DIR, LOGS_DIR
from ..core.sweep import run_sweep
from .ssrfb.runner import SSRFBBenchmark
from .tileqr.runner import TileQRBenchmark

# target ごとの既定値（スレッド既定のみ異なる: tileqr=全コア, ssrfb=1）
_TARGETS = {
    "tileqr": {"factory": TileQRBenchmark, "default_threads": os.cpu_count()},
    "ssrfb": {"factory": SSRFBBenchmark, "default_threads": 1},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plasma_perf bench",
        description="PLASMA タイルQR / SSRFBカーネルの nb・ib スイープ計測",
    )
    parser.add_argument(
        "target", choices=sorted(_TARGETS.keys()),
        help="計測対象（tileqr: フルQR / ssrfb: カーネル単体）",
    )
    parser.add_argument("--exe", type=str, default=None,
                        help="実行ファイル。tileqr は plasmatest（既定: 環境変数 PLASMA_TEST）、"
                             "ssrfb は NoFlush（既定: 環境変数 NOFLUSH_PATH）")
    parser.add_argument("--threads", type=int, default=None,
                        help="OMP_NUM_THREADS（既定: tileqr=全コア, ssrfb=1）。"
                             "明示指定時は出力ファイル名に th<N> を付与")
    parser.add_argument("--size", type=int, default=4096,
                        help="行列サイズ（ssrfb ではCSV記録用メタ。既定 4096）")
    parser.add_argument("--nb-min", type=int, default=32)
    parser.add_argument("--nb-max", type=int, default=512)
    parser.add_argument("--ib-min", type=int, default=8)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--output", type=str, default=None,
                        help="出力CSVパス（省略時は自動命名）")
    parser.add_argument("--timeout-sec", type=float, default=None,
                        help="1点あたりのタイムアウト秒（省略時: ssrfbは動的見積り, tileqrは無制限）")
    parser.add_argument("--assumed-gflops", type=float, default=5.0,
                        help="動的タイムアウト見積り用の下限想定GFlops（既定 5.0）")
    parser.add_argument("--wrapper", type=str, default=None,
                        help='コマンド前置文字列（例: "taskset -c 2"）')
    parser.add_argument("--save-logs", choices=["all", "errors", "none"], default="errors",
                        help="生ログの保存方針（既定 errors）")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    spec = _TARGETS[args.target]
    # threads: 明示指定されたかでファイル名タグの有無を分ける
    threads_explicit = args.threads is not None
    threads = args.threads if threads_explicit else spec["default_threads"]

    bench = spec["factory"](exe=args.exe)

    # 実行ファイルの存在チェック
    exe = os.path.expanduser(bench.exe)
    if not os.path.isfile(exe):
        print(f"[ERROR] 実行ファイルが見つかりません: {exe}\n"
              f"        --exe で指定するか、setup/config.sh で環境変数を設定してください。",
              file=sys.stderr)
        return 1

    output_csv = args.output or build_output_path(
        BENCH_RESULTS_DIR,
        size=args.size, nb_min=args.nb_min, nb_max=args.nb_max, trials=args.trials,
        tag=bench.tag or None,
        threads=(threads if threads_explicit else None),
    )
    log_dir = os.path.join(LOGS_DIR, f"raw_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    run_sweep(
        bench,
        threads=threads, size=args.size,
        nb_min=args.nb_min, nb_max=args.nb_max, ib_min=args.ib_min, step=args.step,
        trials=args.trials,
        output_csv=output_csv, log_dir=log_dir,
        timeout_sec=args.timeout_sec, assumed_gflops=args.assumed_gflops,
        wrapper=args.wrapper, save_logs=args.save_logs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
