"""
model_predict.py — 提案手法のモデル式（正典実装）。

依存なしの純関数のみ。tuning/ の他モジュールや optuna から使われる。

E(nb)・η(nb) の形状のみを扱う（振幅 A は比較にしか使わないため不要）。
定数は tileQR_data 55条件・LOMO検証済み。
詳細は tileQR_research の 2026-09-01_モデル検証まとめ を参照。
"""

# --- モデル定数（クリーン領域2定数版） --------------------------------------
# tileQR_data 55条件・LOMO検証済み。詳細は tileQR_research の 2026-09-01_モデル検証まとめ
S0 = 4096.0
C2 = 3.43
M = 0.19
DELTA = 0.836
K0 = 2.3
THR = 0.92

# nb の探索グリッド（連続最適化は不要なので整数グリッドで評価する）
NB_GRID_MIN = 8
NB_GRID_MAX = 2048


def perf_shape(
    nb,
    beta,
    p,
    size,
    *,
    s0: float = S0,
    c2: float = C2,
    m: float = M,
    delta: float = DELTA,
    k0: float = K0,
):
    """E(nb)・η(nb)。振幅 A は比しか使わないため不要。

    Parameters
    ----------
    nb : int
        タイルサイズ
    beta : float
        SSRFB(dtsmqr代理計測) から推定したオーバーヘッド定数
    p : int
        物理コア数
    size : int
        行列サイズ

    定数は引数で上書きできる（将来の再フィット用）。
    """
    x = c2 * p**m * nb / (s0 * (size / s0) ** delta)
    return nb / (nb + beta) / (1.0 + x**k0)


def _nb_grid(grid_lo: int = NB_GRID_MIN, grid_hi: int = NB_GRID_MAX):
    return range(grid_lo, grid_hi + 1)


def predict_nb(
    beta,
    p,
    size,
    *,
    s0: float = S0,
    c2: float = C2,
    m: float = M,
    delta: float = DELTA,
    k0: float = K0,
    grid_lo: int = NB_GRID_MIN,
    grid_hi: int = NB_GRID_MAX,
):
    """モデルが返す最適 nb（1点）。8〜2048 の整数グリッドで評価する。"""
    best_nb = None
    best_val = -1.0
    for nb in _nb_grid(grid_lo, grid_hi):
        val = perf_shape(nb, beta, p, size, s0=s0, c2=c2, m=m, delta=delta, k0=k0)
        if val > best_val:
            best_val = val
            best_nb = nb
    return best_nb


def predict_band(
    beta,
    p,
    size,
    thr: float = THR,
    *,
    s0: float = S0,
    c2: float = C2,
    m: float = M,
    delta: float = DELTA,
    k0: float = K0,
    grid_lo: int = NB_GRID_MIN,
    grid_hi: int = NB_GRID_MAX,
):
    """探索範囲 (lo, hi)。perf_shape が最大値の thr 倍以上になる nb の範囲。"""
    values = [
        (nb, perf_shape(nb, beta, p, size, s0=s0, c2=c2, m=m, delta=delta, k0=k0))
        for nb in _nb_grid(grid_lo, grid_hi)
    ]
    peak = max(v for _, v in values)
    above = [nb for nb, v in values if v >= thr * peak]
    return min(above), max(above)
