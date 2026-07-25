"""env.py — サブプロセスに渡す環境変数を組み立てる。

benchmark ごとにスレッド方針が異なるため、フラグで挙動を切り替える:

- ssrfb（カーネル単体）: threads=1 で BLAS のネスト並列を抑止し、LD_PRELOAD も除去する。
  → build_env(threads, pin_blas=True, drop_ld_preload=True)
- tileqr（フルQR）: PLASMA 自身が並列化するため OMP_NUM_THREADS のみ設定する
  （従来 run_time_dgeqrf の挙動を保持）。
  → build_env(threads, pin_blas=False, drop_ld_preload=False)
"""
import os


def build_env(threads: int, pin_blas: bool = False, drop_ld_preload: bool = False) -> dict:
    """計測用の環境変数辞書を返す（現在の環境を複製して上書き）。"""
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(threads)
    if pin_blas:
        env["MKL_NUM_THREADS"] = "1"
        env["OPENBLAS_NUM_THREADS"] = "1"
    if drop_ld_preload:
        env.pop("LD_PRELOAD", None)
    return env
