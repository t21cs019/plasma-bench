"""__main__.py — 1ソケット全コア同時実行の ssrfb スイープ計測。

    python -m plasma_perf.benchmarks.ssrfb_parallel [オプション]

例:
    # 構成の確認だけ（使うコア・点数・所要時間の見積もり）
    PYTHONPATH=src python3 -m plasma_perf.benchmarks.ssrfb_parallel --size 4096 --dry-run
    # ソケット0の全物理コアで size=4096
    PYTHONPATH=src python3 -m plasma_perf.benchmarks.ssrfb_parallel --size 4096
    # 比較用: 同じ N_IT の決め方で1本だけ（m=1）
    PYTHONPATH=src python3 -m plasma_perf.benchmarks.ssrfb_parallel --size 4096 --cpus 0

nb・ib のグリッドは1コア計測（plasma_perf bench ssrfb）と同じ既定値
（nb 32-512, ib 8-nb//2, step 4）。CSV の先頭6列も1コア計測と同じで、
その後ろに n_par, cpu, n_it, end_skew_sec が続く（1行 = 1コアの1点）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

from . import runner
from ..ssrfb.runner import N_IT_DEFAULT, flops_ssrfb, resolve_exe
from ...core.csv_io import build_output_path, failure_reason, save_raw_log, save_rows
from ...core.paths import BENCH_RESULTS_DIR, LOGS_DIR
from ...core.process import ProcResult
from ...core.sweep import count_points
from ...core.throttle import ThrottleMonitor

FIELDNAMES = ["threads", "size", "nb", "ib", "Time_sec", "GFlops",
              "n_par", "cpu", "n_it", "end_skew_sec"]

# 1点あたりの起動・行列初期化のおおよその上乗せ（所要時間の見積もり用）
_OVERHEAD_SEC = 0.5


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m plasma_perf.benchmarks.ssrfb_parallel",
        description="NoFlush を1ソケットの全物理コアで同時実行する ssrfb の nb・ib スイープ",
    )
    p.add_argument("--exe", type=str, default=None,
                   help="NoFlush（既定: 環境変数 NOFLUSH_PATH → ~/Library/Tune_SSRFB/NoFlush）")
    p.add_argument("--size", type=int, default=4096, help="NoFlush の MAT_SIZE（既定 4096）")
    p.add_argument("--socket", type=int, default=0, help="使うソケット（既定 0）")
    p.add_argument("--cpus", type=str, default=None,
                   help='使う論理CPUを明示（例 "0-31"）。指定時は --socket を無視')
    p.add_argument("--target-sec", type=float, default=2.0,
                   help="1点の計時区間の目標秒数。N_IT をこれに合わせて決める（既定 2.0）")
    p.add_argument("--n-it-min", type=int, default=N_IT_DEFAULT,
                   help=f"N_IT の下限（既定 {N_IT_DEFAULT} = 1コア計測と同じ）")
    p.add_argument("--nb-min", type=int, default=32)
    p.add_argument("--nb-max", type=int, default=512)
    p.add_argument("--ib-min", type=int, default=8)
    p.add_argument("--ib-max", type=int, default=None, help="ib の上限（既定: nb//2）")
    p.add_argument("--step", type=int, default=4)
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--output", type=str, default=None, help="出力CSVパス（省略時は自動命名）")
    p.add_argument("--assumed-gflops", type=float, default=1.0,
                   help="タイムアウト見積り用の下限想定 GFlops/コア（既定 1.0）")
    p.add_argument("--wrapper", type=str, default=None,
                   help='taskset の前に置くコマンド（例 "numactl --membind=0-3"）')
    p.add_argument("--save-logs", choices=["all", "errors", "none"], default="errors")
    p.add_argument("--throttle-check", action="store_true",
                   help="core_throttle_count を nb ごとに確認する（AMD では多くの場合無効）")
    p.add_argument("--dry-run", action="store_true",
                   help="使うコア・点数・所要時間の見積もりを表示して終了")
    return p


def mem_available_bytes() -> Optional[int]:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def write_meta(path: str, args: argparse.Namespace, cpus: List[int], exe: str) -> None:
    """計測条件の控え（使ったCPU・引数・lscpu）を CSV の隣に残す。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lscpu = subprocess.run(["lscpu"], capture_output=True, text=True).stdout
    topo = subprocess.run(["lscpu", "-p=CPU,CORE,SOCKET,NODE"],
                          capture_output=True, text=True).stdout
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"started: {datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"exe: {exe}\n")
        f.write(f"n_par: {len(cpus)}\n")
        f.write(f"cpus: {','.join(map(str, cpus))}\n")
        f.write(f"wrapper: {args.wrapper or ''}\n")
        f.write(f"args: {vars(args)}\n")
        f.write("\n# === lscpu ===\n" + lscpu)
        f.write("\n# === lscpu -p=CPU,CORE,SOCKET,NODE ===\n" + topo)


def _ib_range(nb: int, args: argparse.Namespace) -> range:
    return range(args.ib_min, (args.ib_max if args.ib_max is not None else nb // 2) + 1, args.step)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    exe = os.path.expanduser(resolve_exe(args.exe))

    cpus = runner.parse_cpu_list(args.cpus) if args.cpus else runner.detect_socket_cpus(args.socket)
    if not cpus:
        print("[ERROR] 使えるCPUがありません（--socket / --cpus と現在の affinity を確認）",
              file=sys.stderr)
        return 1
    n_par = len(cpus)

    points = count_points(args.nb_min, args.nb_max, args.ib_min, args.step, args.ib_max)
    n_nb = len(range(args.nb_min, args.nb_max + 1, args.step))
    est_h = (points * (args.target_sec + _OVERHEAD_SEC) + n_nb * 2 * _OVERHEAD_SEC) \
        * args.trials / 3600
    need = n_par * args.size * args.size * 8
    avail = mem_available_bytes()

    print("=" * 60)
    print(" ssrfb 全コア同時実行ベンチマーク（nb・ib スイープ）")
    print(f"  n_par        : {n_par}")
    print(f"  cpus         : {','.join(map(str, cpus))}")
    print(f"  size         : {args.size}  (MAT_SIZE)")
    print(f"  nb           : {args.nb_min} ～ {args.nb_max}  step={args.step}")
    print(f"  ib           : {args.ib_min} ～ {'nb//2' if args.ib_max is None else args.ib_max}"
          f"      step={args.step}")
    print(f"  trials       : {args.trials}")
    print(f"  points/trial : {points}")
    print(f"  N_IT         : 計時区間 ≈ {args.target_sec} 秒（下限 {args.n_it_min}）")
    print(f"  wrapper      : {args.wrapper or '(なし)'}")
    print(f"  memory       : {need / 2**30:.1f} GiB 必要"
          f" / 空き {'不明' if avail is None else f'{avail / 2**30:.1f} GiB'}")
    print(f"  所要時間     : 約 {est_h:.1f} 時間（見積もり）")
    print("=" * 60)

    if avail is not None and need > avail * 0.8:
        print("[ERROR] メモリが足りません。--size を小さくするか --cpus で本数を減らしてください。",
              file=sys.stderr)
        return 1
    if not os.path.isfile(exe):
        print(f"[ERROR] NoFlush が見つかりません: {exe}", file=sys.stderr)
        return 1 if not args.dry_run else 0
    if args.dry_run:
        return 0

    output_csv = args.output or build_output_path(
        BENCH_RESULTS_DIR, size=args.size, nb_min=args.nb_min, nb_max=args.nb_max,
        trials=args.trials, tag=f"ssrfb-par{n_par}",
    )
    base = os.path.splitext(output_csv)[0]
    write_meta(base + "_meta.txt", args, cpus, exe)
    log_dir = os.path.join(LOGS_DIR, f"raw_par_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    throttle = ThrottleMonitor(enabled=args.throttle_check, log_path=base + "_throttle.log")
    print(f"  output       : {output_csv}")

    env = runner.make_env()
    write_header = not os.path.exists(output_csv)
    missing = 0
    nb = args.nb_min
    throttle.start()

    try:
        for trial in range(1, args.trials + 1):
            if args.trials > 1:
                print(f"\n--- trial {trial}/{args.trials} ---")
            done = 0
            for nb in range(args.nb_min, args.nb_max + 1, args.step):
                buffer = []
                per_iter = None
                for ib in _ib_range(nb, args):
                    if per_iter is None:
                        # nb の最初の点: 1本だけ短く回して1回あたり時間を見積もる
                        calib = runner.run_parallel_point(
                            exe, nb, ib, size=args.size, n_it=args.n_it_min, cpus=cpus[:1],
                            env=env, wrapper=args.wrapper,
                            timeout=runner.point_timeout(nb, ib, args.n_it_min,
                                                         args.assumed_gflops),
                        )
                        per_iter = calib[0].time_sec
                    n_it = runner.choose_n_it(per_iter, args.target_sec, args.n_it_min)

                    results = runner.run_parallel_point(
                        exe, nb, ib, size=args.size, n_it=n_it, cpus=cpus,
                        env=env, wrapper=args.wrapper,
                        timeout=runner.point_timeout(nb, ib, n_it, args.assumed_gflops),
                    )
                    skew = runner.end_skew_sec(results)
                    ok = [r for r in results if r.time_sec is not None]
                    for r in results:
                        t = r.time_sec
                        g = flops_ssrfb(nb, ib) / t / 1e9 if t else float("nan")
                        buffer.append([1, args.size, nb, ib, t if t else -1.0, g,
                                       n_par, r.cpu, n_it, round(skew, 4)])
                        if not t:
                            missing += 1
                        if args.save_logs == "all" or (args.save_logs == "errors" and not t):
                            save_raw_log(os.path.join(log_dir, f"cpu{r.cpu}"), nb, ib, trial,
                                         ProcResult(r.returncode, r.stdout, r.stderr, r.timed_out))
                    done += 1

                    # 次の点の N_IT は、同時実行で最も遅かったコアの1回あたり時間から決める
                    per_iter = max(r.time_sec for r in ok) if ok else None

                    if ok:
                        gs = sorted(flops_ssrfb(nb, ib) / r.time_sec / 1e9 for r in ok)
                        print(f"  [{done}/{points}] nb={nb:4d} ib={ib:4d} n_it={n_it:6d}"
                              f" -> GFlops/core min={gs[0]:.3g} med={gs[len(gs) // 2]:.3g}"
                              f" max={gs[-1]:.3g}  ok={len(ok)}/{n_par} skew={skew:.2f}s")
                    else:
                        reasons = {failure_reason(ProcResult(r.returncode, r.stdout, r.stderr,
                                                             r.timed_out)) for r in results}
                        print(f"  [{done}/{points}] nb={nb:4d} ib={ib:4d}"
                              f" -> 全コア欠測 ({','.join(sorted(reasons))})")

                save_rows(buffer, output_csv, FIELDNAMES, write_header)
                write_header = False
                throttle.check(f"nb={nb}")
    except KeyboardInterrupt:
        print("\n[中断] Ctrl-C を検知しました。ここまでの結果は保存済みです。")
        print(f"       再開する場合は --nb-min {nb} 以降を指定して再実行してください。")
        return 130

    throttle.summary()
    print(f"\n[完了] 結果を保存しました: {output_csv}")
    print(f"       欠測: {missing} 行（全 {points * args.trials * n_par} 行中）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
