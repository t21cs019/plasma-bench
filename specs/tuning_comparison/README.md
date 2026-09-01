# チューニング手法の実測比較（tuning/）

元指示書: [`INSTRUCTIONS_tuning_comparison.md`](../../INSTRUCTIONS_tuning_comparison.md)（リポジトリ直下）。

## 目的

`agullo`（卒論の既存手法）・`optuna_full`（卒論の Optuna）・`model`（本研究の提案手法）の
3手法を、**正解を知らない状態から何秒で何 GFlops に到達するか**という観点で実測比較する。
既存の `benchmark.py`（nb×ib 全スイープで正解表を作るツール）とは別物。

最初の対象機: **i3-10100**（卒論 T21CS019 と同一機種）。

## 実装時に確定した実装判断（指示書だけでは決まらなかった点）

指示書は `tileQR_bench`（このリポジトリの旧称）を前提に書かれているが、実装時点でこの
リポジトリは既にモノレポ再構成（コミット `9e40e54`）を経ていた。差分を吸収するために
以下を決めた。

1. **`src/run_time_dgeqrf.py` は存在しない** → `src/plasma_perf/benchmarks/tileqr/runner.py`
   の `measure_gflops(nb, ib, *, threads, size)` を使う（`tuning/methods/*.py` から import）。
2. **NoFlush（dtsmqr/SSRFB カーネル計測）の実際の CLI 契約を実機で確認**した結果、
   指示書執筆時に想定されていた「1起動で ib 範囲を一括計測できる」仕様
   （`NoFlush [NB] [min IB] [max IB]`）ではなく、実機のバイナリは
   `NoFlush [NB] [IB] [MAT_SIZE=4096] [N_IT=50]` で **1起動につき (nb, ib) 1点のみ**を
   返す契約だった（`plasma-workspace/tune_ssrfb/NoFlush.cpp` で確認、実行してエラーになる
   ことも確認済み）。これは既存の `src/plasma_perf/benchmarks/ssrfb/runner.py` と同じ契約
   なので、`tuning/kernel_bench.py` はそれをそのまま再利用するように書き直した
   （詳細は `tuning/kernel_bench.py` 冒頭のコメント）。
3. **`--kernel-reps` / `--beta-reps`（卒論の「50回実行して平均」）の意味**: 外部から
   Python で NoFlush を50回起動して平均する実装にすると、agullo のフルグリッド計測
   （nb約127点×ib約64点）だけで数万〜数十万プロセス起動になり非現実的（指示書5節の
   見積り約80分を大幅に超過する）。NoFlush 自身が `N_IT` 引数でカーネルを N_IT 回実行し
   平均する機能を既に持つため、**reps はそのまま N_IT に渡す**（2026-09-01 ユーザ確認）。
   1点=1プロセス起動で完結し、指示書の想定時間に近い規模を維持できる。
4. **依存管理**: 指示書は `tuning/requirements.txt` を想定していたが、このリポジトリは
   既に `pyproject.toml` の `[project.optional-dependencies]` + `uv` で依存を管理している
   （`tuning` extra に optuna、`viz` extra に pandas/pyarrow を追加した）。それに合わせた。
5. **dtsmqr/SSRFB カーネル計測のスレッド数バグ（2026-09-01 ユーザ指摘・修正）**:
   初期実装では agullo のグリッド計測・model のβ計測に、campaign の `--threads`
   （フルQR測定用、既定 `os.cpu_count()`）をそのまま渡してしまっていた。既存の
   `src/plasma_perf/benchmarks/cli.py` の `_DEFAULT_THREADS = {"tileqr": os.cpu_count(),
   "ssrfb": 1}` の通り、SSRFB/dtsmqrカーネル単体の計測は常に1スレッドが規約。
   `--kernel-threads`（既定1）を独立の CLI 引数として追加し、`--threads` とは
   完全に切り離した（QR測定は引き続き `--threads` を使う）。**このバグの影響で
   size=4096 のフル実行1回目を実行中に agullo が想定（学部研究比約5000秒）の
   2倍以上（約11570秒/回）かかっていることが判明した**（OpenMPランタイム初期化
   コストが約8256回のNoFlush起動それぞれに乗ったためと推測）。バグ修正後の
   フル実行はまだ行っていない。
6. **stdout のブロックバッファリングでログの進捗表示が大幅に遅延する問題
   （2026-09-01 実機で発覚・修正）**: `run_tuning.py` を `scripts/run_campaign.sh`
   経由（`| tee ログ`）で実行すると、標準出力先が非tty（パイプ）になるため
   CPython の既定動作でブロックバッファに切り替わり、`print()` による進捗表示
   （"--- run" / "-> nb=" 等）が何時間も flush されずログに反映されない現象を
   確認した（size=4096 の本番実行で約7時間・agullo 2回分の進捗が一切ログに
   出ていなかった。CSV 書き込みは1行ごとに明示 flush されるため実害は無かった）。
   `run_tuning.py` の先頭で `sys.stdout.reconfigure(line_buffering=True)` を呼び、
   非tty でも行バッファ（1行ごとに flush）になるよう修正した。

ヒューリスティック2の条件2・3の解釈、dtsmqr の flops 式（暫定的に `4*nb^3` を流用）等、
指示書が「曖昧なら notes 列かコード内 docstring に明記してよい」としている点は
`tuning/methods/agullo.py` と `tuning/kernel_bench.py` の docstring を参照。

## セットアップ

```bash
bash scripts/setup_python.sh          # uv + optuna/pandas/pyarrow 等を導入（.venv/）
```

## 実行手順（i3-10100）

```bash
# 0. SMT 状態を確認（正解表が SMT-off なので off に揃える）
lscpu | grep -i "Thread(s) per core"

# 1. 見積り確認（実行回数・推定時間。実測は行わない）
uv run python tuning/run_tuning.py --dry-run --size 4096 --methods agullo,optuna_full,model

# 2. 短縮版で動作確認（repeat=1, agullo の kernel-reps を落とす）
uv run python tuning/run_tuning.py --size 4096 --methods model --repeat 1 --threads 4
uv run python tuning/run_tuning.py --size 4096 --methods agullo --repeat 1 --threads 4 --kernel-reps 5

# 3. 本番（tmux ラッパー経由。SSH 切断に耐える）
SIZE=4096 REPEAT=10 THREADS=4 bash scripts/run_tuning_campaign.sh
# 複数サイズを逐次実行する場合:
SIZES="1024 2048 4096" REPEAT=10 THREADS=4 bash scripts/run_tuning_campaign.sh

tmux attach -t tuning       # 進捗確認（Ctrl-b d で離脱）
tail -f logs/<最新>.log
```

`agullo` はデフォルト設定（kernel-reps=50, nb 4〜512 step4, ib 2〜nb/2 step2）だと
1回あたり見積りで約80分かかる（`--dry-run` で確認）。指示書5節の通り、必要なら
`--kernel-reps` を下げるか agullo だけ `--repeat` を落とす（その場合は CSV の
`notes` 列にその旨が残る運用にはなっていないため、実行コマンドと合わせて記録すること）。

出力:

- `results/tuning/tuning_<host>_size<S>_th<N>_<ts>.csv` — 各手法・各 run の結果
- `results/tuning/beta_<host>_<ts>.csv` — β 推定に使った SSRFB 生データ
- `results/tuning/tuning_<host>_size<S>_th<N>_<ts>.provenance/` — `lscpu.txt` /
  `smt_active.txt` / `provenance.json`（git rev・物理コア数・SMT状態など）

## オフライン評価（手元で実行。データがある側）

```bash
bash scripts/setup_python.sh --extra viz    # pandas/pyarrow のみで足りる

uv run python tuning/evaluate.py \
    --results results/tuning/tuning_i3-10100_size4096_th4_*.csv \
    --qr-sweep ../tileQR_data/derived/qr_sweep.parquet \
    --config i3-10100_s1_smt-off
```

正解表（`tileQR_data/derived/qr_sweep.parquet`）の SMT 状態と実測 CSV の `smt_active`
列が食い違うとエラーで停止する（黙って比較しない）。正解表に無い (nb, ib) は
最近傍 nb で代用し、明細 CSV の `match_flag` 列にその旨を残す（`--output` で保存可）。

## 動作確認済みの範囲（2026-09-01）

実機（i3-10100, size=1024, 小さい nb/ib 範囲・低い trial 数）で `agullo` /
`optuna_full` / `model` の3手法・`run_tuning.py` の CSV 出力・provenance 保存・
`evaluate.py` の正解表突き合わせまで一通り動作確認済み。

その後、size=4096・本番規模の初回フル実行を開始したところ、実行中に
「実装判断」5・6節のバグ（dtsmqr/SSRFBカーネル計測のスレッド数、stdout の
ブロックバッファリング）が発覚したため、その回は中断せず参考データとして
走らせつつコードのみ修正した（tmuxセッションは操作していない）。**この
バグ修正版でのフル実行はまだ行っていない**。次回実行時に stdout の
行バッファ化・`--kernel-threads=1` が効いているか、進捗ログがリアルタイムに
流れるかを確認すること。
