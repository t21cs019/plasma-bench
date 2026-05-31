"""
heatmap.py — ベンチマーク結果CSVからnb×ibのGFLOPSヒートマップを生成する

使い方:
    python -m viz.heatmap --input results/benchmark/xxx.csv
    python -m viz.heatmap --input results/benchmark/xxx.csv --output heatmap.png
    python -m viz.heatmap --input results/benchmark/xxx.csv --threads 16
"""
import argparse
import os
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt


def make_heatmap(input_csv: str, output_png: str, threads: int = None) -> None:
    df = pd.read_csv(input_csv)

    # スレッド数でフィルタリング
    if threads is not None:
        df = df[df["threads"] == threads]
        if df.empty:
            print(f"[ERROR] threads={threads} のデータが見つかりません。")
            print(f"        利用可能なスレッド数: {pd.read_csv(input_csv)['threads'].unique()}")
            return

    # ピボットテーブルに変換（行: ib, 列: nb, 値: GFlops）
    heatmap_data = df.pivot_table(index="ib", columns="nb", values="GFlops", aggfunc="mean")
    heatmap_data = heatmap_data.iloc[::-1]  # 縦軸を反転（ibが大きい方を上に）

    # 最大値とその位置を取得
    max_gflops = heatmap_data.max().max()
    max_position = heatmap_data.stack().idxmax()
    max_ib, max_nb = max_position

    # ヒートマップを作成
    plt.figure(figsize=(12, 8))
    ax = sns.heatmap(
        heatmap_data,
        annot=False,
        cmap="YlGnBu",
        cbar_kws={"label": "GFlop/s"},
    )

    # 最大値の位置を赤丸で強調
    plt.scatter(
        heatmap_data.columns.get_loc(max_nb) + 0.5,
        heatmap_data.index.get_loc(max_ib) + 0.5,
        color="red",
        s=100,
        label=f"Max: {max_gflops:.2f} GFlop/s\n(nb={max_nb}, ib={max_ib})",
        zorder=5,
    )
    plt.legend(loc="upper left", bbox_to_anchor=(1.15, 1))

    basename = os.path.splitext(os.path.basename(input_csv))[0]
    title = f"Heatmap of GFlop/s — {basename}"
    if threads is not None:
        title += f" (threads={threads})"
    plt.title(title)

    plt.xlabel("nb (tile size)")
    plt.ylabel("ib (inner block size)")
    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close()

    print(f"[完了] ヒートマップを保存しました: {output_png}")
    print(f"       最大値: nb={max_nb}, ib={max_ib}, GFlops={max_gflops:.4f}")


def parse_args():
    parser = argparse.ArgumentParser(description="ベンチマーク結果ヒートマップ生成")
    parser.add_argument("--input",   required=True, help="入力CSVファイルパス")
    parser.add_argument("--output",  default=None,  help="出力PNGファイルパス")
    parser.add_argument("--threads", type=int, default=None, help="フィルタするスレッド数")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.output is None:
        base = os.path.splitext(args.input)[0]
        suffix = f"_threads{args.threads}" if args.threads else ""
        args.output = f"{base}{suffix}_heatmap.png"
    make_heatmap(args.input, args.output, args.threads)
