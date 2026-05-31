"""
benchmark.py — nb・ib を総当たりで GFLOPS 計測して CSV に保存する

使い方:
    python -m benchmark.benchmark [オプション]

オプション:
    --threads   OMP_NUM_THREADS（デフォルト: CPUコア数）
    --size      行列サイズ（デフォルト: 4096）
    --nb-min    nb の最小値（デフォルト: 32）
    --nb-max    nb の最大値（デフォルト: 512）
    --ib-min    ib の最小値（デフォルト: 8）
    --step      nb・ib のステップ幅（デフォルト: 4）
    --output    出力CSVファイルパス（デフォルト: results/benchmark/自動命名）
    --trials    同じ条件で何回計測するか（デフォルト: 1）
"""
import argparse
import csv
import os
import socket
import datetime

from core.run_time_dgeqrf import run_time_dgeqrf

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "results", "benchmark")


def save_to_csv(rows: list, output_csv: str, write_header: bool) -> None:
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    with open(output_csv, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["threads", "size", "nb", "ib", "GFlops"])
        writer.writerows(rows)


def run_benchmark(
    output_csv: str,
    size: int,
    nb_min: int,
    nb_max: int,
    ib_min: int,
    step: int,
    num_threads: int,
    trials: int,
) -> None:
    print("=" * 54)
    print(" ベンチマーク設定")
    print(f"  threads : {num_threads}")
    print(f"  size    : {size}")
    print(f"  nb      : {nb_min} ～ {nb_max}  step={step}")
    print(f"  ib      : {ib_min} ～ nb//2     step={step}")
    print(f"  trials  : {trials}")
    print(f"  output  : {output_csv}")
    print("=" * 54)

    write_header = not os.path.exists(output_csv)
    buffer = []

    for trial in range(1, trials + 1):
        if trials > 1:
            print(f"\n--- trial {trial}/{trials} ---")

        for nb in range(nb_min, nb_max + 1, step):
            for ib in range(ib_min, nb // 2 + 1, step):
                gflops = run_time_dgeqrf(num_threads, size, nb, ib)
                buffer.append([num_threads, size, nb, ib, gflops])
                print(f"  nb={nb:4d}, ib={ib:4d} -> {gflops:.3f} GFLOPS")

            # nb ごとに CSV へ書き出し（途中でクラッシュしてもデータを保持）
            save_to_csv(buffer, output_csv, write_header)
            buffer.clear()
            write_header = False

    print(f"\n[完了] 結果を保存しました: {output_csv}")


def build_output_path(args) -> str:
    """引数から自動でファイル名を生成する"""
    hostname = socket.gethostname()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = (
        f"{hostname}_size{args.size}"
        f"_nb{args.nb_min}-{args.nb_max}"
        f"_ib{args.ib_min}"
        f"_step{args.step}"
        f"_{timestamp}.csv"
    )
    return os.path.join(RESULTS_DIR, filename)


def parse_args():
    parser = argparse.ArgumentParser(description="PLASMA dgeqrf 総当たりベンチマーク")
    parser.add_argument("--threads", type=int, default=os.cpu_count())
    parser.add_argument("--size",    type=int, default=4096)
    parser.add_argument("--nb-min",  type=int, default=32)
    parser.add_argument("--nb-max",  type=int, default=512)
    parser.add_argument("--ib-min",  type=int, default=8)
    parser.add_argument("--step",    type=int, default=4)
    parser.add_argument("--output",  type=str, default=None)
    parser.add_argument("--trials",  type=int, default=1)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    output = args.output or build_output_path(args)
    run_benchmark(
        output_csv=output,
        size=args.size,
        nb_min=args.nb_min,
        nb_max=args.nb_max,
        ib_min=args.ib_min,
        step=args.step,
        num_threads=args.threads,
        trials=args.trials,
    )
