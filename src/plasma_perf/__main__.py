"""__main__.py — plasma_perf の統一CLIディスパッチャ。

    python -m plasma_perf bench {tileqr,ssrfb} [オプション]
    python -m plasma_perf tune  {normal,select,ssrfb,all} [オプション]
    python -m plasma_perf viz   heatmap [オプション]

サブコマンドごとに遅延 import する（bench は標準ライブラリのみ、
tune/viz は optuna / matplotlib 等の optional 依存を必要とするため）。
"""
import sys
from typing import List, Optional

_USAGE = """使い方: python -m plasma_perf <command> ...

commands:
  bench {tileqr,ssrfb}          nb・ib スイープ計測
  plan  {init,scan,add,run}     計測進捗の管理と未計測分の自動実行
  tune  {normal,select,ssrfb,all}  Optuna チューニング（要 [tuning]）
  viz   heatmap                 結果の可視化（要 [viz]）

例:
  python -m plasma_perf bench tileqr --size 4096 --trials 5
  python -m plasma_perf bench ssrfb --exe /path/to/NoFlush --trials 5 --throttle-check
  python -m plasma_perf plan add ssrfb --sizes 4096,8192 --threads 1
  python -m plasma_perf plan run --dry-run
  python -m plasma_perf tune ssrfb --counts 5 --trials 20
  python -m plasma_perf viz heatmap --input results/benchmark/xxx.csv
"""


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if argv else 1

    command, rest = argv[0], argv[1:]

    if command == "bench":
        from .benchmarks.cli import main as bench_main
        return bench_main(rest)
    if command == "plan":
        from .plan.cli import main as plan_main
        return plan_main(rest)
    if command == "tune":
        from .tuning.cli import main as tune_main
        return tune_main(rest)
    if command == "viz":
        from .viz.cli import main as viz_main
        return viz_main(rest)

    print(f"[ERROR] 未知のコマンド: {command}\n", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
