"""cli.py — `python -m plasma_perf viz heatmap` のエントリポイント。

例:
    python -m plasma_perf viz heatmap --input results/benchmark/xxx.csv
    python -m plasma_perf viz heatmap --input results/benchmark/xxx.csv --threads 16
"""
from __future__ import annotations

import argparse
import os
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plasma_perf viz",
        description="計測結果の可視化",
    )
    sub = parser.add_subparsers(dest="kind", required=True)

    hp = sub.add_parser("heatmap", help="nb×ib の GFLOPS ヒートマップ")
    hp.add_argument("--input", required=True, help="入力CSVファイルパス")
    hp.add_argument("--output", default=None, help="出力PNGファイルパス")
    hp.add_argument("--threads", type=int, default=None, help="フィルタするスレッド数")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.kind == "heatmap":
        from .heatmap import make_heatmap

        output = args.output
        if output is None:
            base = os.path.splitext(args.input)[0]
            suffix = f"_threads{args.threads}" if args.threads else ""
            output = f"{base}{suffix}_heatmap.png"
        make_heatmap(args.input, output, args.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
