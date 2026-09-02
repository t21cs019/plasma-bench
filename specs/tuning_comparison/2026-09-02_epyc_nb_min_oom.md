# EPYC実機での nb_min OOM調査メモ (2026-09-02)

このリポジトリでのチューニング比較機能（[`INSTRUCTIONS_tuning_comparison.md`](../../INSTRUCTIONS_tuning_comparison.md)）を
EPYCサーバー（`epyc`, AMD EPYC 7543 32-Core, 物理コア32, SMT off, メモリ125GB）で
初めて実行した際に発生したOOM（メモリ枯渇）の調査記録。このコミットで加えた
`--nb-min-optuna` / `--nb-min` の上書きオプションの背景説明を兼ねる。

## 発生した問題

`model,optuna_full,agullo` の順で短縮版動作確認（`--repeat 1`）を実行したところ、
**2回にわたってメモリがほぼ枯渇**（125GB中123GB使用、スワップ50GB超）する事態が発生した。

- **1回目（`optuna_full` 実行中）**: `plasmatest dgeqrf --dim=4096x4096 --nb=4 --ib=2` が
  **RSS 約123GB** を消費。`optuna_full` の探索空間 `nb ∈ [4, 512]`（卒論通りの設定）のうち
  `nb=4` を Optuna が試行したことが原因。
- **2回目（`agullo` 実行中）**: 同じく `plasmatest dgeqrf --dim=4096x4096 --nb=4 --ib=2` が再発。
  `agullo` のヒューリスティック2が凸包の端点として `nb=4` を選択し、最終QRベンチ
  （`measure_gflops`）でこの点を実行したことが原因。

いずれもユーザー確認のうえプロセスをkillして停止し、メモリは正常に復帰した。

## 原因の技術的推定

PLASMAのタイルQRは、実行グラフ（DAGスケジューラ）のタスク数がタイル数の3乗
**`(size/nb)^3`** のオーダーで増加すると推定される。

```
size=4096 の場合:
  nb=4  → (1024)^3 ≈ 10.7億タスク  ← OOMの原因
  nb=8  → (512)^3  ≈ 1.34億タスク  ← まだ危険域
  nb=16 → (256)^3  ≈ 1677万タスク ← 安全域と判断
  nb=32 → (128)^3  ≈ 210万タスク
```

実測の123GBという桁も、10億オーダーのタスク記述子（1タスクあたり数十〜百バイト級）が
積み上がったと考えれば整合する。

## 対応方針とこのコミットでの変更

指示書に「`agullo` と `optuna_full` は卒論の設定を変えないこと」と明記されているため、
**コード上のデフォルト値（`nb_min=4`）は変更していない**。代わりに、CLI引数で実行時だけ
上書きできるようにした。上書きした場合は結果CSVの `notes` 列にその旨を自動で記録する
（卒論再現から逸脱している事実が後から追跡できるように）。

- `tuning/methods/optuna_full.py`: `nb_min`/`nb_max` が卒論デフォルト（4, 512）と異なる場合、
  `notes` に逸脱の説明を記録するようにした（`nb_min`/`nb_max` を kwargs として受け取る実装は
  元から存在した）。
- `tuning/methods/agullo.py`: 同様に `select_heuristic2()` が返す `note` に逸脱の説明を追記する
  ようにした（`--nb-min` CLI引数は元から存在した）。
- `tuning/run_tuning.py`: `optuna_full` にはこれまで `nb_min`/`nb_max` をCLIから渡す経路が
  無かったため、`--nb-min-optuna` / `--nb-max-optuna` を新設し `optuna_kwargs` に渡すようにした。

EPYCでこの機能を実行する際は、`--nb-min-optuna 16 --nb-min 16` の指定を推奨する
（`nb=16`は上記の見積りで安全域に入る）。

## 検証状況（2026-09-02時点）

- `optuna_full` 側の修正（`--nb-min-optuna 16`）: **動作確認済み**。
  size=4096, threads=32 で `nb=188, ib=52, ≈549 GFlops` を正常に得られ、メモリも安定していた。
- `agullo` 側の修正（`--nb-min 16`）: **まだ一度も検証できていない**。
  検証実行を開始したが、`model` フェーズ終了直後・`agullo` 開始前に作業都合で停止したため、
  実際にOOMを回避できるかは未確認のまま。

## 次にやるべきこと

1. `agullo` だけを単独で（`--methods agullo --repeat 1 --kernel-reps 5 --nb-min 16`）動かし、
   メモリを監視しながら `--nb-min 16` がOOMを回避できるか検証する。
2. 念のため `select_heuristic2` の bin 分割ロジックが `nb_min` を正しく尊重し、`nb_min` 未満の
   点を絶対に選ばないことをコードレビューで確認する。
3. 安全性が確認できたら本番の `scripts/run_tuning_campaign.sh` 実行
   （`METHODS="model,optuna_full,agullo"`, `EXTRA_ARGS="--nb-min-optuna 16 --nb-min 16"` 等）に進む。
