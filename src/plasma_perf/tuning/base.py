"""base.py — Optuna チューニング共通ユーティリティ（保存・可視化）。"""
import csv
import os


def save_best_trial(
    counts: int,
    elapsed: float,
    best_params: dict,
    best_value: float,
    output_file: str,
) -> None:
    """最適な試行の結果をCSVファイルに保存する。"""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    write_header = not os.path.exists(output_file)
    with open(output_file, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["Trial", "Execution Time (s)", "nb", "ib", "Objective Value"])
        writer.writerow([counts, elapsed, best_params["nb"], best_params["ib"], best_value])


def visualize_study_results(
    best_params: dict,
    best_value: float,
    study,
    output_dir: str,
) -> None:
    """Optuna の最適化結果を可視化して画像として保存する（plotly + kaleido が必要）。"""
    import optuna.visualization as vis  # optional 依存なので関数内 import

    os.makedirs(output_dir, exist_ok=True)

    fig = vis.plot_optimization_history(study)
    fig.add_annotation(
        text=f"Best Params: {best_params}<br>Best Value: {best_value:.4f}",
        xref="paper", yref="paper",
        x=1.05, y=1.05,
        showarrow=False,
        font=dict(size=12, color="black"),
        align="center",
        bordercolor="black", borderwidth=1,
    )
    fig.write_image(os.path.join(output_dir, "optimization_history.png"))

    fig = vis.plot_slice(study)
    fig.write_image(os.path.join(output_dir, "slice_plot.png"))

    fig = vis.plot_parallel_coordinate(study)
    fig.write_image(os.path.join(output_dir, "parallel_coordinate.png"))

    fig = vis.plot_contour(study)
    fig.write_image(os.path.join(output_dir, "contour_plot.png"))
