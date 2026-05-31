"""
run_time_dgeqrf.py — PLASMAのdgeqrfを実行してGFLOPSを返す
"""
import subprocess
import re
import os

# インストール先は環境変数から取得（setup/config.sh で設定）
_PLASMA_TEST = os.environ.get(
    "PLASMA_TEST",
    os.path.expanduser("~/Library/plasma/bin/plasmatest")
)


def run_time_dgeqrf(num_threads: int, size: int, nb_value: int, ib_value: int) -> float:
    """
    plasmatest dgeqrf を実行して GFLOPS 値を返す。

    Parameters
    ----------
    num_threads : int
        OMP_NUM_THREADS に設定するスレッド数
    size : int
        行列サイズ（正方行列 size x size）
    nb_value : int
        タイルサイズ nb
    ib_value : int
        内部ブロック幅 ib

    Returns
    -------
    float
        GFLOPS 値。取得失敗時は 0.0
    """
    command = [
        _PLASMA_TEST,
        "dgeqrf",
        f"--dim={size}x{size}",
        f"--nb={nb_value}",
        f"--ib={ib_value}",
    ]

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(num_threads)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
    )

    match = re.search(
        r"pass\s+([-\d.e]+)\s+([-\d.e]+)\s+([\d.]+)",
        result.stdout,
    )

    if match:
        return float(match.group(3))
    else:
        print(f"[WARN] GFLOPS 未取得: threads={num_threads}, nb={nb_value}, ib={ib_value}")
        if result.stderr:
            print(f"       stderr: {result.stderr.strip()}")
        return 0.0
