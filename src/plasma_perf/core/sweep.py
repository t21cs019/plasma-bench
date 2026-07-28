"""sweep.py — nb・ib スイープ計測の共通エンジン。

benchmark（tileqr / ssrfb）を受け取り、nb x ib を総当たりで実行して CSV に保存する。
tileqr / ssrfb の差（コマンド・出力パース・列構成・タイムアウト）は Benchmark 側に
閉じ込めてあるため、このループは1つで両方をまかなえる。

設計は ssrfb 版 benchmark.py を汎用化したもの:
  - nb ごとに CSV へフラッシュ（途中クラッシュでもデータを保持）
  - 欠測（非ゼロ終了・パース失敗・タイムアウト）を落とさず記録して継続
  - Ctrl-C で安全に中断し、再開方法を案内
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional, Sequence

from .csv_io import failure_reason, save_raw_log, save_rows
from .throttle import ThrottleMonitor
from ..benchmarks.base import Benchmark


def count_points(nb_min: int, nb_max: int, ib_min: int, step: int) -> int:
    return sum(
        len(range(ib_min, nb // 2 + 1, step))
        for nb in range(nb_min, nb_max + 1, step)
    )


def csv_fieldnames(benchmark: Benchmark) -> List[str]:
    return ["threads", "size", "nb", "ib"] + list(benchmark.value_fields)


def _format_values(values: dict, fields: Sequence[str]) -> str:
    return ", ".join(f"{f}={values.get(f):.6g}" for f in fields)


def run_sweep(
    benchmark: Benchmark,
    *,
    threads: int,
    size: int,
    nb_min: int,
    nb_max: int,
    ib_min: int,
    step: int,
    trials: int,
    output_csv: str,
    log_dir: str,
    timeout_sec: Optional[float] = None,
    assumed_gflops: float = 5.0,
    wrapper: Optional[str] = None,
    save_logs: str = "errors",
    throttle_check: bool = False,
    throttle_abort: bool = False,
) -> int:
    """スイープを実行し、欠測点数を返す。"""
    env = benchmark.make_env(threads)
    fieldnames = csv_fieldnames(benchmark)
    points_per_trial = count_points(nb_min, nb_max, ib_min, step)

    throttle_log = os.path.splitext(output_csv)[0] + "_throttle.log"
    throttle = ThrottleMonitor(
        enabled=throttle_check, abort=throttle_abort, log_path=throttle_log
    )

    print("=" * 60)
    print(f" {benchmark.name} ベンチマーク（nb・ib スイープ）")
    print(f"  threads      : {threads}")
    print(f"  size         : {size}")
    print(f"  nb           : {nb_min} ～ {nb_max}  step={step}")
    print(f"  ib           : {ib_min} ～ nb//2      step={step}")
    print(f"  trials       : {trials}")
    print(f"  points/trial : {points_per_trial}")
    print(f"  timeout      : {'動的見積もり' if timeout_sec is None else f'{timeout_sec}秒固定'}")
    print(f"  wrapper      : {wrapper or '(なし)'}")
    print(f"  save-logs    : {save_logs}")
    print(f"  throttle     : {'ON' if throttle_check else 'OFF'}"
          f"{'（検知で中断）' if throttle_check and throttle_abort else ''}")
    print(f"  output       : {output_csv}")
    print("=" * 60)

    write_header = not os.path.exists(output_csv)
    missing = 0
    nb = nb_min
    throttle.start()

    try:
        for trial in range(1, trials + 1):
            if trials > 1:
                print(f"\n--- trial {trial}/{trials} ---")
            done = 0

            for nb in range(nb_min, nb_max + 1, step):
                buffer = []
                for ib in range(ib_min, nb // 2 + 1, step):
                    timeout = (
                        timeout_sec
                        if timeout_sec is not None
                        else benchmark.estimate_timeout(nb, ib, assumed_gflops)
                    )
                    result = benchmark.run_point(
                        nb, ib,
                        threads=threads, size=size, env=env,
                        timeout=timeout, wrapper=wrapper,
                    )
                    row = [threads, size, nb, ib] + [
                        result.values.get(f) for f in benchmark.value_fields
                    ]
                    buffer.append(row)
                    done += 1

                    if result.ok:
                        print(
                            f"  [{done}/{points_per_trial}] nb={nb:4d} ib={ib:4d}"
                            f" -> {_format_values(result.values, benchmark.value_fields)}"
                        )
                    else:
                        missing += 1
                        print(
                            f"  [{done}/{points_per_trial}] nb={nb:4d} ib={ib:4d}"
                            f" -> 欠測 ({failure_reason(result.proc)})"
                        )

                    if save_logs == "all" or (save_logs == "errors" and not result.ok):
                        save_raw_log(log_dir, nb, ib, trial, result.proc)

                # nb ごとに CSV へ書き出し
                save_rows(buffer, output_csv, fieldnames, write_header)
                write_header = False

                # nb の区切りでスロットリングを確認
                if throttle.check(f"nb={nb}") and throttle_abort:
                    print("\n[中断] throttling を検知したため計測を停止します"
                          "（--throttle-abort）。ここまでの結果は保存済みです。")
                    throttle.summary()
                    sys.exit(3)
    except KeyboardInterrupt:
        print("\n[中断] Ctrl-C を検知しました。ここまでの結果は保存済みです。")
        print(f"       再開する場合は --nb-min {nb} 以降を指定して再実行してください。")
        sys.exit(130)

    throttle.summary()
    print(f"\n[完了] 結果を保存しました: {output_csv}")
    print(f"       欠測: {missing} 点（全 {points_per_trial * trials} 点中）")
    return missing
