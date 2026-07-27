"""heatmap.py — ベンチマーク結果CSVから nb×ib の GFLOPS ヒートマップを生成する。"""
import os


def make_heatmap(input_csv: str, output_png: str, threads: int = None) -> None:
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

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
    heatmap_data = heatmap_data.iloc[::-1]  # 縦軸を反転（ib が大きい方を上に）

    max_gflops = heatmap_data.max().max()
    max_position = heatmap_data.stack().idxmax()
    max_ib, max_nb = max_position

    plt.figure(figsize=(12, 8))
    sns.heatmap(
        heatmap_data,
        annot=False,
        cmap="YlGnBu",
        cbar_kws={"label": "GFlop/s"},
    )

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
