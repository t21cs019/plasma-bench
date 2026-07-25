"""base.py — benchmark の共通インタフェース。

各 benchmark（tileqr / ssrfb）は Benchmark を継承し、
「環境変数の作り方・1点の実行・（必要なら）タイムアウト見積り」だけを実装する。
スイープのループ本体と CSV 書き出しは core.sweep が担う。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core.process import ProcResult


@dataclass
class PointResult:
    """(nb, ib) 1点の計測結果。

    values : CSV に記録する計測値の辞書。列名 -> 値。
             例) tileqr: {"GFlops": 28.5} / ssrfb: {"Time_sec": .., "GFlops": ..}
             欠測時も列は埋める（GFlops=NaN, Time_sec=-1 等）。
    ok     : 有効な計測が取れたか（False なら欠測としてカウント）。
    proc   : 生の実行結果（失敗理由の分類・生ログ保存に使う）。
    """

    values: Dict[str, float]
    ok: bool
    proc: ProcResult


class Benchmark(ABC):
    name: str = ""            # "tileqr" / "ssrfb"
    tag: str = ""             # 出力ファイル名のタグ（tileqr は "")
    value_fields: List[str] = []  # CSV の計測値カラム

    @abstractmethod
    def make_env(self, threads: int) -> dict:
        """この benchmark 用の環境変数辞書を返す。"""

    @abstractmethod
    def run_point(
        self,
        nb: int,
        ib: int,
        *,
        threads: int,
        size: int,
        env: dict,
        timeout: Optional[float],
        wrapper: Optional[str],
    ) -> PointResult:
        """(nb, ib) を1点実行して PointResult を返す。"""

    def estimate_timeout(self, nb: int, ib: int, assumed_gflops: float) -> Optional[float]:
        """動的タイムアウト（省略時 None=無制限）。必要な benchmark だけ override。"""
        return None
