"""throttle.py — サーマルスロットリング検知。

Linux の per-core カウンタ
    /sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count
を読み、計測中に値が増えたら「throttling が起きた」と判断する。
（`grep -H "" /sys/.../core_throttle_count` と同じ情報を Python で直接読む。）

カウンタが無いシステム（一部の AMD 等）では available() が False を返し、
計測は続行する（クラッシュさせない）。
"""
from __future__ import annotations

import glob
import os
import re
from typing import Dict, Optional

_GLOB = "/sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count"
_CPU_RE = re.compile(r"/cpu(\d+)/")


def available() -> bool:
    return len(glob.glob(_GLOB)) > 0


def read_counts() -> Dict[str, int]:
    """{cpu ラベル: カウンタ値} を返す。読めないものは飛ばす。"""
    counts: Dict[str, int] = {}
    for path in sorted(glob.glob(_GLOB)):
        m = _CPU_RE.search(path)
        label = f"cpu{m.group(1)}" if m else path
        try:
            with open(path) as f:
                counts[label] = int(f.read().strip())
        except (OSError, ValueError):
            continue
    return counts


def _delta(base: Dict[str, int], now: Dict[str, int]) -> Dict[str, int]:
    return {k: now[k] - base.get(k, 0) for k in now if now[k] - base.get(k, 0) > 0}


class ThrottleMonitor:
    """計測ループに差し込むスロットリング監視。

    start() で基準値、check(label) で前回チェックからの増分を検出し、
    増えていれば警告表示＋ログ追記して True を返す。summary() で総括。
    enabled=False（既定）または カウンタ非対応なら何もしない。
    """

    def __init__(
        self,
        enabled: bool = False,
        abort: bool = False,
        log_path: Optional[str] = None,
    ):
        self.requested = enabled
        self.available = available()
        self.enabled = enabled and self.available
        self.abort = abort
        self.log_path = log_path
        self.base: Dict[str, int] = {}
        self.last: Dict[str, int] = {}

    def start(self) -> None:
        if not self.requested:
            return
        if not self.available:
            print("[throttle] このシステムには core_throttle_count がありません。"
                  "スロットリング監視はスキップします（計測は続行）。")
            return
        self.base = read_counts()
        self.last = dict(self.base)
        print(f"[throttle] 監視開始（{len(self.base)} コア）。")

    def check(self, label: str) -> bool:
        """前回チェック以降に throttling があれば True。"""
        if not self.enabled:
            return False
        now = read_counts()
        delta = _delta(self.last, now)
        self.last = now
        if delta:
            self._log(label, delta)
            shown = ", ".join(f"{k}:+{v}" for k, v in sorted(delta.items()))
            print(f"[throttle] ⚠ {label} で throttling 検知: {shown}")
            return True
        return False

    def summary(self) -> None:
        if not self.enabled:
            return
        total = _delta(self.base, self.last)
        if total:
            shown = ", ".join(f"{k}:+{v}" for k, v in sorted(total.items()))
            print(f"[throttle] 総括: throttling 発生 — {shown}")
        else:
            print("[throttle] 総括: throttling は検知されませんでした。")

    def _log(self, label: str, delta: Dict[str, int]) -> None:
        if not self.log_path:
            return
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            for k, v in sorted(delta.items()):
                f.write(f"{label}\t{k}\t+{v}\n")
