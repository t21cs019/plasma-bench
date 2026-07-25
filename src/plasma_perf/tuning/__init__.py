"""tuning — Optuna 等によるパラメータ（nb, ib）チューニング。

目的関数は benchmarks.*.runner の measure_gflops / measure_time を再利用するため、
計測スイープと同一の実行経路になる（結果の一貫性）。
optuna は optional 依存（`pip install -e ".[tuning]"`）。
"""
