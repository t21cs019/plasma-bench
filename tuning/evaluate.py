"""
evaluate.py — オフライン評価スクリプト。

run_tuning.py の出力CSVを、tileQR_data の正解表（既存スイープの平均値）と
突き合わせて評価する（指示書 4節）。**実行環境ではなく手元（tileQR_data を
読める側）で動かす**。pandas/pyarrow が必要
（`bash scripts/setup_python.sh --extra viz` で導入。計測機側には不要）。

正解表に無い (nb, ib) が返された場合:
    最近傍の nb を採用する（推定で埋めない。match_flag="nearest_nb" を立てる）。
    その nb で指定 ib がそのまま存在すればそれを使い、無ければ MaxIB
    （その nb での最良 ib）を採用する。
    ※ 指示書は「最近傍のnbを使う」とのみ記載しており ib の扱いは明示されて
      いないため、この解釈を採用した（要検証）。

SMT 不一致チェック:
    正解表の config 文字列（例: i3-10100_s1_smt-off）が持つ smt-on/off と、
    実測 CSV の smt_active 列が食い違ったらエラーで停止する（黙って比較しない）。

使い方:
    uv run python tuning/evaluate.py \\
        --results results/tuning/tuning_i3-10100_size4096_th4_*.csv \\
        --qr-sweep ../tileQR_data/derived/qr_sweep.parquet \\
        --config i3-10100_s1_smt-off
"""
import argparse
import glob
import os
import sys

import pandas as pd


def load_results(patterns) -> pd.DataFrame:
    paths = []
    for pat in patterns:
        matched = sorted(glob.glob(pat))
        if not matched and os.path.isfile(pat):
            matched = [pat]
        paths.extend(matched)
    if not paths:
        raise SystemExit(f"[ERROR] 結果CSVが見つかりません: {patterns}")

    frames = [pd.read_csv(p) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    return df


def check_smt_consistency(results_df: pd.DataFrame, config: str):
    if "smt-off" in config:
        expected = False
    elif "smt-on" in config:
        expected = True
    else:
        raise SystemExit(f"[ERROR] config から smt-on/off を判定できません: {config}")

    smt_col = results_df["smt_active"].astype(str).str.lower()
    actual_states = set(smt_col.unique())
    mismatched = {"true", "1"} if not expected else {"false", "0"}
    if actual_states & mismatched:
        raise SystemExit(
            f"[ERROR] SMT状態が正解表(config={config}, smt-{'on' if expected else 'off'})と"
            f"食い違っています。実測 smt_active={sorted(actual_states)}。"
            "黙って比較しないため停止します。"
        )


def find_ground_truth(qr_sweep: pd.DataFrame, nb: int, ib: int):
    """(nb, ib) の正解表突き合わせ。

    Returns
    -------
    (gflops_true, match_flag) : (float | None, str)
    """
    exact = qr_sweep[(qr_sweep["nb"] == nb) & (qr_sweep["ib"] == ib)]
    if len(exact) > 0:
        return float(exact["GFlops"].mean()), "exact"

    available_nb = qr_sweep["nb"].unique()
    if len(available_nb) == 0:
        return None, "no_data"
    nearest_nb = min(available_nb, key=lambda x: abs(x - nb))

    at_nearest = qr_sweep[qr_sweep["nb"] == nearest_nb]
    same_ib = at_nearest[at_nearest["ib"] == ib]
    if len(same_ib) > 0:
        return float(same_ib["GFlops"].mean()), "nearest_nb"

    # MaxIB（その nb での最良 ib の平均GFlopsが最大のもの）
    by_ib_mean = at_nearest.groupby("ib")["GFlops"].mean()
    return float(by_ib_mean.max()), "nearest_nb_maxib"


def evaluate(results_df: pd.DataFrame, qr_sweep: pd.DataFrame) -> pd.DataFrame:
    peak = float(qr_sweep["GFlops"].max())

    rows = []
    for _, r in results_df.iterrows():
        if pd.isna(r.get("nb")) or pd.isna(r.get("ib")):
            rows.append({**r.to_dict(), "gflops_true": None, "ratio": None, "match_flag": "no_pair"})
            continue
        nb, ib = int(r["nb"]), int(r["ib"])
        gflops_true, match_flag = find_ground_truth(qr_sweep, nb, ib)
        ratio = gflops_true / peak if gflops_true is not None else None
        rows.append({**r.to_dict(), "gflops_true": gflops_true, "ratio": ratio, "match_flag": match_flag, "peak": peak})

    return pd.DataFrame(rows)


def summarize(evaluated_df: pd.DataFrame) -> pd.DataFrame:
    """卒論 表5.1 と同じ形式（手法ごとの ratio 統計 + 時間平均）で集計する。"""
    g = evaluated_df.groupby("method")
    summary = g.agg(
        n_runs=("run_id", "count"),
        ratio_mean=("ratio", "mean"),
        ratio_min=("ratio", "min"),
        ratio_std=("ratio", "std"),
        time_total_sec_mean=("time_total_sec", "mean"),
        time_prep_sec_mean=("time_prep_sec", "mean"),
    ).reset_index()
    return summary


def parse_args():
    p = argparse.ArgumentParser(description="tuning/run_tuning.py 出力のオフライン評価")
    p.add_argument("--results", nargs="+", required=True, help="結果CSV（glob可、複数可）")
    p.add_argument("--qr-sweep", type=str, required=True, help="tileQR_data/derived/qr_sweep.parquet")
    p.add_argument("--config", type=str, required=True, help="正解表の config 列（例: i3-10100_s1_smt-off）")
    p.add_argument("--size", type=int, default=None, help="サイズで絞り込む（省略時はCSVの値を使用）")
    p.add_argument("--output", type=str, default=None, help="評価済み明細CSVの保存先（任意）")
    return p.parse_args()


def main():
    args = parse_args()

    results_df = load_results(args.results)
    check_smt_consistency(results_df, args.config)

    sizes = results_df["size"].unique()
    if args.size is None:
        if len(sizes) > 1:
            raise SystemExit(f"[ERROR] 結果CSVに複数サイズが混在しています: {sizes}。--size で指定してください。")
        size = int(sizes[0])
    else:
        size = args.size

    qr_sweep_all = pd.read_parquet(args.qr_sweep)
    qr_sweep = qr_sweep_all[(qr_sweep_all["config"] == args.config) & (qr_sweep_all["size"] == size)]
    if len(qr_sweep) == 0:
        raise SystemExit(
            f"[ERROR] 正解表に該当データがありません: config={args.config}, size={size}。"
            f"利用可能な config: {sorted(qr_sweep_all['config'].unique())}"
        )

    evaluated = evaluate(results_df[results_df["size"] == size], qr_sweep)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        evaluated.to_csv(args.output, index=False)
        print(f"[info] 評価済み明細を保存しました: {args.output}")

    nearest_count = (evaluated["match_flag"] != "exact").sum()
    if nearest_count > 0:
        print(f"[WARN] 正解表に無い (nb,ib) が {nearest_count}/{len(evaluated)} 件ありました"
              "（最近傍nbで代用。match_flag列参照）")

    summary = summarize(evaluated)
    print("\n=== 手法比較（卒論 表5.1 形式） ===")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
