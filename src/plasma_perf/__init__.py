"""plasma_perf — PLASMA タイルQR（dgeqrf）と DSSRFB/DTSMQR カーネルの
計測・チューニング・可視化を集約したツールキット。

サブパッケージ:
    core        計測の共有基盤（プロセス実行・CSV I/O・スイープエンジン等）
    benchmarks  ベンチマーク（tileqr: フルQR / ssrfb: カーネル単体）
    tuning      Optuna 等によるパラメータチューニング
    viz         結果の可視化

統一CLI:
    python -m plasma_perf bench {tileqr,ssrfb} [オプション]
    python -m plasma_perf tune  {normal,select,ssrfb,all} [オプション]
    python -m plasma_perf viz   heatmap [オプション]
"""

__version__ = "0.1.0"
