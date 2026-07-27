"""process.py — サブプロセスを1回実行する共通ラッパー（shell=True は使わない）。

tileqr（plasmatest）も ssrfb（NoFlush）も、この `run_process` を通して実行する。
計測固有の知識（コマンド生成・出力パース・GFlops計算）は各 benchmark 側に置き、
ここは「コマンドを実行して素の結果を返す」ことだけを担う。
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass
class ProcResult:
    """プロセス実行の素の結果。時間や GFlops といった計測値は含まない。"""

    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False


def run_process(
    command: Sequence[str],
    env: Optional[dict] = None,
    timeout: Optional[float] = None,
    wrapper: Optional[str] = None,
) -> ProcResult:
    """`command`（リスト形式）を1回実行して ProcResult を返す。

    Parameters
    ----------
    command : Sequence[str]
        実行するコマンド（例: [exe, "256", "64"]）。
    env : dict, optional
        サブプロセスに渡す環境変数（省略時は継承）。
    timeout : float, optional
        タイムアウト秒（省略時は無制限）。超過時は timed_out=True で返す。
    wrapper : str, optional
        コマンド前置文字列（例 "taskset -c 2"）。shlex で分割して連結する。
    """
    prefix: List[str] = shlex.split(wrapper) if wrapper else []
    cmd = prefix + list(command)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return ProcResult(
            returncode=None,
            stdout=e.stdout or "",
            stderr=e.stderr or "",
            timed_out=True,
        )

    return ProcResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        timed_out=False,
    )
