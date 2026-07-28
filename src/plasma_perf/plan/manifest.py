"""manifest.py — 進捗ファイル PROGRESS.md（ssrfb / tileqr / tuning の3表）の
データモデルと、マークダウンの読み書き（読み込み時は計画列・note を保持、
書き込み時は桁を自動整形）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

_HEADER = """# 計測進捗（plasma-perf）

<!--
  このファイルは `python -m plasma_perf plan scan` が status/coverage を自動更新します。
  計画列（status/coverage 以外）と note 列は手動編集できます。
  行の追加は `plan add` が簡単です（例: plan add ssrfb --sizes 4096,8192 --threads 1）。
  note に skip / manual と書くと `plan run` はその行を実行しません。
  nb は "min-max"（例 32-512）で書きます。
-->
"""

_SSRFB_COLS = ["size", "threads", "n_it", "nb", "ib_min", "step", "trials",
               "status", "coverage", "note"]
_TILEQR_COLS = ["size", "threads", "nb", "ib_min", "step", "trials",
                "status", "coverage", "note"]
_TUNE_COLS = ["mode", "size", "threads", "n_trials", "counts",
              "status", "coverage", "note"]


@dataclass
class BenchRow:
    kind: str            # "ssrfb" | "tileqr"
    size: int
    threads: int
    nb_min: int
    nb_max: int
    ib_min: int
    step: int
    trials: int
    n_it: Optional[int] = None   # ssrfb のみ
    status: str = "?"
    coverage: str = ""
    note: str = ""

    @property
    def nb(self) -> str:
        return f"{self.nb_min}-{self.nb_max}"

    def key(self):
        return (self.kind, self.size, self.threads, self.n_it,
                self.nb_min, self.nb_max, self.ib_min, self.step, self.trials)


@dataclass
class TuneRow:
    mode: str            # normal | select | ssrfb
    size: int
    threads: int
    n_trials: int
    counts: int
    status: str = "?"
    coverage: str = ""
    note: str = ""

    def key(self):
        return (self.mode, self.size, self.threads, self.n_trials, self.counts)


@dataclass
class Manifest:
    ssrfb: List[BenchRow] = field(default_factory=list)
    tileqr: List[BenchRow] = field(default_factory=list)
    tuning: List[TuneRow] = field(default_factory=list)

    def actionable(self):
        """実行対象（missing/partial かつ note が skip/manual でない行）を順に返す。"""
        for row in list(self.ssrfb) + list(self.tileqr) + list(self.tuning):
            if row.note.strip().lower() in ("skip", "manual"):
                continue
            if row.status in ("missing", "partial"):
                yield row


# --------------------------------------------------------------------------
# パース
# --------------------------------------------------------------------------
def _parse_nb(text: str):
    text = text.strip()
    if "-" in text:
        a, b = text.split("-", 1)
        return int(a), int(b)
    v = int(text)
    return v, v


def _split_row(line: str) -> List[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def _table_rows(lines: List[str], start: int):
    """start 以降の連続する '|' 行（ヘッダ+区切り+データ）からデータ行を返す。"""
    rows = []
    i = start
    seen = 0
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        seen += 1
        if seen > 2:  # 1=ヘッダ, 2=区切り, 3以降=データ
            rows.append(lines[i])
        i += 1
    return rows, i


def parse(text: str) -> Manifest:
    lines = text.splitlines()
    m = Manifest()
    i = 0
    section = None
    while i < len(lines):
        line = lines[i].strip()
        low = line.lower()
        if low.startswith("## "):
            name = low[3:].strip()
            section = name if name in ("ssrfb", "tileqr", "tuning") else None
            i += 1
            continue
        if section and line.startswith("|"):
            data, nxt = _table_rows(lines, i)
            for d in data:
                try:
                    _parse_into(m, section, _split_row(d))
                except (ValueError, IndexError):
                    continue  # 壊れた行は飛ばす
            i = nxt
            section = None
            continue
        i += 1
    return m


def _parse_into(m: Manifest, section: str, c: List[str]) -> None:
    if section == "ssrfb":
        nb_min, nb_max = _parse_nb(c[3])
        m.ssrfb.append(BenchRow(
            kind="ssrfb", size=int(c[0]), threads=int(c[1]),
            n_it=int(c[2]), nb_min=nb_min, nb_max=nb_max,
            ib_min=int(c[4]), step=int(c[5]), trials=int(c[6]),
            note=c[9] if len(c) > 9 else "",
        ))
    elif section == "tileqr":
        nb_min, nb_max = _parse_nb(c[2])
        m.tileqr.append(BenchRow(
            kind="tileqr", size=int(c[0]), threads=int(c[1]),
            nb_min=nb_min, nb_max=nb_max, ib_min=int(c[3]),
            step=int(c[4]), trials=int(c[5]),
            note=c[8] if len(c) > 8 else "",
        ))
    elif section == "tuning":
        m.tuning.append(TuneRow(
            mode=c[0], size=int(c[1]), threads=int(c[2]),
            n_trials=int(c[3]), counts=int(c[4]),
            note=c[7] if len(c) > 7 else "",
        ))


# --------------------------------------------------------------------------
# レンダリング（桁揃え）
# --------------------------------------------------------------------------
def _render_table(cols: List[str], rows: List[List[str]]) -> str:
    widths = [len(c) for c in cols]
    for r in rows:
        for j, cell in enumerate(r):
            widths[j] = max(widths[j], len(cell))
    def fmt(cells):
        return "| " + " | ".join(cell.ljust(widths[j]) for j, cell in enumerate(cells)) + " |"
    out = [fmt(cols), "| " + " | ".join("-" * widths[j] for j in range(len(cols))) + " |"]
    out += [fmt(r) for r in rows]
    return "\n".join(out)


def _bench_cells(row: BenchRow, cols: List[str]) -> List[str]:
    values = {
        "size": str(row.size), "threads": str(row.threads),
        "n_it": "" if row.n_it is None else str(row.n_it),
        "nb": row.nb, "ib_min": str(row.ib_min), "step": str(row.step),
        "trials": str(row.trials), "status": row.status,
        "coverage": row.coverage, "note": row.note,
    }
    return [values[c] for c in cols]


def _tune_cells(row: TuneRow) -> List[str]:
    return [row.mode, str(row.size), str(row.threads), str(row.n_trials),
            str(row.counts), row.status, row.coverage, row.note]


def render(m: Manifest) -> str:
    parts = [_HEADER, "## ssrfb", "",
             _render_table(_SSRFB_COLS, [_bench_cells(r, _SSRFB_COLS) for r in m.ssrfb]),
             "", "## tileqr", "",
             _render_table(_TILEQR_COLS, [_bench_cells(r, _TILEQR_COLS) for r in m.tileqr]),
             "", "## tuning", "",
             _render_table(_TUNE_COLS, [_tune_cells(r) for r in m.tuning]),
             ""]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------
def load(path: str) -> Manifest:
    import os
    if not os.path.exists(path):
        return Manifest()
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def save(path: str, m: Manifest) -> None:
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(m))
