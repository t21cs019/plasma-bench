"""ssrfb_parallel — 1ソケットの全物理コアで NoFlush を同時実行する ssrfb 計測。

既存の1コア計測（benchmarks/ssrfb, `plasma_perf bench ssrfb`）には手を入れず、
同じ NoFlush・同じ nb×ib グリッドを別系統として計測する。

    python -m plasma_perf.benchmarks.ssrfb_parallel --size 4096 --dry-run
"""
