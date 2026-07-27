"""csv_io.py — 計測結果CSVの書き出しと、失敗点の生ログ保存。

CSV は benchmark ごとに列が異なる（tileqr: GFlops のみ / ssrfb: Time_sec, GFlops）。
このモジュールは列名を引数で受け取り、スキーマ非依存で書き出す。
"""
import csv
import os
import socket
from datetime import datetime
from typing import List, Optional, Sequence

from .process import ProcResult


def save_rows(
    rows: Sequence[Sequence], output_csv: str, fieldnames: Sequence[str], write_header: bool
) -> None:
    """rows を CSV に追記する。write_header が真ならヘッダ行を先に書く。"""
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    with open(output_csv, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(list(fieldnames))
        writer.writerows(rows)


def build_output_path(
    results_dir: str,
    size: int,
    nb_min: int,
    nb_max: int,
    trials: int,
    tag: Optional[str] = None,
    threads: Optional[int] = None,
    host: Optional[str] = None,
) -> str:
    """自動命名の出力CSVパスを組み立てる。

    形式: {host}[_{tag}]_size{size}_nb{min}-{max}[_th{threads}]_t{trials}_{timestamp}.csv
    （tag は ssrfb のときのみ付与。threads は明示指定時のみ付与。）
    """
    host = host or socket.gethostname()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts: List[str] = [host]
    if tag:
        parts.append(tag)
    parts.append(f"size{size}")
    parts.append(f"nb{nb_min}-{nb_max}")
    if threads is not None:
        parts.append(f"th{threads}")
    parts.append(f"t{trials}")
    parts.append(timestamp)
    return os.path.join(results_dir, "_".join(parts) + ".csv")


def failure_reason(proc: ProcResult) -> str:
    """欠測点の失敗理由を短い文字列で返す。"""
    if proc.timed_out:
        return "timeout"
    if proc.returncode not in (0, None):
        return f"rc={proc.returncode}"
    return "parse-fail"


def save_raw_log(log_dir: str, nb: int, ib: int, trial: int, proc: ProcResult) -> str:
    """失敗点（またはsave-logs=all時）の生 stdout/stderr をファイルに残す。"""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"log_nb{nb}_ib{ib}_t{trial}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# returncode: {proc.returncode}\n")
        f.write(f"# timed_out: {proc.timed_out}\n")
        if proc.returncode == -9 and not proc.timed_out:
            f.write(
                "# NOTE: SIGKILL(-9) を検知。OOM killer による強制終了の可能性あり"
                "（大きい nb x ib で T 行列が肥大化するため）。\n"
            )
        f.write("\n# === STDOUT ===\n")
        f.write(proc.stdout or "")
        f.write("\n# === STDERR ===\n")
        f.write(proc.stderr or "")
    return path
