# 2026-11-11_tau（EPYC 7543）

タスク1個ごとの時間 τ を確かめる追加計測。背景と目的は Vault の `weekly/2026-11-11.md`「追加で行う計測」。ファイルの番号はそこの番号と同じ。

| スクリプト | 内容 | 条件 |
|---|---|---|
| `1_thread_scaling.sh` | スレッド数だけを変えて QR を測る | p = 4, 8, 16, 32 / size 4096, 8192 / nb 32〜256 / ib = nb/4 / 5回 / 固定あり |
| `2_task_overhead.sh` | 空のタスクで τ を測る | p = 1〜32 / タイル 64, 128, 256 / 中身 0 と 1000 ns / libgomp と libiomp5 / 5プロセス × 3回 |
| `3_repeat.sh` | 同じプロセスで3回分解する | p = 32 / size 2048, 4096 / nb 192, 256 / ib = 32 / 固定あり・なし / 5プロセス |

変数で条件を上書きできる（例 `REPS=1`）。

## 流し方

リポジトリの直下で実行する。他の計測と同時に流さない。

1. 短い条件で、3本とも最後まで動くか確かめる

   ```bash
   C=scripts/hpc/epyc7543/campaigns/2026-11-11_tau
   SIZES=1024 NB_LIST=128 PROCS=1 bash $C/3_repeat.sh
   THREADS="4 32" SIZES=1024 NB_LIST="64 128" REPS=1 bash $C/1_thread_scaling.sh
   THREADS="4 32" TILES=32 WORK_NS=0 REPS=1 ITERS=1 bash $C/2_task_overhead.sh
   ```

2. `meta.txt` の `ldd` の行で、カーネルが OpenBLAS か MKL かを確かめる

3. 本番を 3 → 1 → 2 の順に流す（SSH が切れても続くように tmux で）。別々に起動すると同時に走るので、1行でつなぐ

   ```bash
   SESSION=tau scripts/local/run_campaign.sh -- bash -c "bash $C/3_repeat.sh && bash $C/1_thread_scaling.sh && bash $C/2_task_overhead.sh"
   ```

   進み具合は `tmux attach -t tau` で見る（`Ctrl-b d` で離脱）。

## 結果

`results/benchmark/epyc_s1_smt-off_<実験名>_<時刻>/` に、実行ごとに1ディレクトリできる。まず `summary.txt` を見る。
