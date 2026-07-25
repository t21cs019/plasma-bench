# plasma-perf

PLASMA タイルQR分解（`dgeqrf`）と、その構成カーネル **DSSRFB/DTSMQR** の
**計測・チューニング・可視化**を集約したツールキット。

`tileQR_bench`（フルQR計測）と `ssrfb_bench`（カーネル単体計測）、および Optuna
チューニングを1つのモノレポに統合したもの。共通の計測基盤（`core`）を1か所に持ち、
tileqr / ssrfb の差分だけを各 `benchmarks/` に閉じ込めている。

- 計測コア（`core` / `benchmarks`）は **Python 標準ライブラリのみ**で動作（システム python3 で動く）
- `shell=True` を使わない（サブプロセスはリスト形式で起動）
- 1点ごとにタイムアウトを設け、ハングで全体が止まらない
- チューニング（`tuning`）と可視化（`viz`）は optional 依存

> **環境構築（OpenBLAS / PLASMA / NoFlush のビルド）は別リポジトリ
> [`plasma-workspace`](https://github.com/t21cs019/plasma-workspace) が担当する。**
> このリポジトリは Python の計測・チューニング・可視化のみを持つ。

## 位置づけ（2リポジトリ構成）

```
plasma-workspace/   ← ネイティブ環境ビルド専用（マシンごとに1回）
  setup/            OpenBLAS / PLASMA / NoFlush のビルド, config.sh, check
plasma-perf/        ← このリポジトリ（Python を集約）
  src/plasma_perf/  core / benchmarks / tuning / viz
```

2つは **submodule ではなく、隣り合わせに clone** して使う（`run_campaign.sh` が
`../plasma-workspace/setup/config.sh` を自動で読み、`PLASMA_TEST` / `NOFLUSH_PATH`
等の環境変数を供給する）。

## セットアップ

```bash
# 1. 環境ビルド（別リポジトリ。マシンごとに1回）
git clone https://github.com/t21cs019/plasma-workspace.git
cd plasma-workspace && bash setup/install.sh && bash setup/check.sh && cd ..

# 2. このリポジトリを隣に clone
git clone https://github.com/t21cs019/plasma-perf.git
cd plasma-perf

# 3a. 計測だけなら追加インストール不要（標準ライブラリで動く）
# 3b. チューニング・可視化を使う場合のみ:
python3 -m pip install -e ".[tuning,viz]"
```

## 統一CLI

すべて `python -m plasma_perf <command>` で呼ぶ（`src/` を PYTHONPATH に載せる）。

```bash
export PYTHONPATH=src

# --- 計測（bench）---
python -m plasma_perf bench tileqr --size 4096 --trials 5
python -m plasma_perf bench ssrfb  --exe /path/to/NoFlush --trials 5

# --- チューニング（tune）---
python -m plasma_perf tune ssrfb  --counts 5 --trials 20
python -m plasma_perf tune select --nb-min 192 --nb-max 336 --counts 10 --trials 50

# --- 可視化（viz）---
python -m plasma_perf viz heatmap --input results/benchmark/xxx.csv --threads 16
```

本番計測・チューニングは tmux ラッパー経由で回すと SSH 切断に耐える（後述）。

### bench のオプション（`plasma_perf bench {tileqr,ssrfb}`）

| オプション | 説明 | 既定値 |
|---|---|---|
| `target` | `tileqr`（フルQR）/ `ssrfb`（カーネル単体）| 必須 |
| `--exe` | 実行ファイル。tileqr=plasmatest（既定は環境変数 `PLASMA_TEST`）、ssrfb=NoFlush（既定は `NOFLUSH_PATH`）| 環境変数 |
| `--threads` | OMP_NUM_THREADS。明示時はファイル名に `th<N>` を付与 | tileqr=全コア / ssrfb=1 |
| `--size` | 行列サイズ（ssrfb ではCSV記録用メタ）| `4096` |
| `--nb-min`/`--nb-max`/`--ib-min`/`--step` | スイープ範囲 | `32`/`512`/`8`/`4` |
| `--trials` | 計測回数 | `1`（本番は5推奨）|
| `--output` | 出力CSVパス | 省略時は自動命名 |
| `--timeout-sec` | 1点のタイムアウト秒 | 省略時 ssrfb=動的見積り / tileqr=無制限 |
| `--assumed-gflops` | 動的タイムアウト見積り用の下限想定GFlops | `5.0` |
| `--wrapper` | コマンド前置文字列（例 `"taskset -c 2"`）| なし |
| `--save-logs` | 生ログ保存方針 `{all, errors, none}` | `errors` |

## 本番計測（tmux ラッパー）

`scripts/run_campaign.sh` は **任意のコマンドを tmux + ログ tee で包む汎用ラッパー**。
計測対象は `--` 以降のコマンドで指定する（tileqr / ssrfb / tune で共用）。

```bash
scripts/run_campaign.sh -- python -m plasma_perf bench tileqr --size 4096 --trials 5
scripts/run_campaign.sh -- python -m plasma_perf bench ssrfb  --exe ~/Library/Tune_SSRFB/NoFlush --trials 5
SESSION=tune scripts/run_campaign.sh -- python -m plasma_perf tune ssrfb --counts 5 --trials 20

tmux attach -t bench       # 進捗確認（Ctrl-b d で離脱）
tail -f logs/<最新>.log
```

結果の転送:

```bash
bash scripts/sync_results.sh rclone   # OneDrive 等へ
SCP_DEST='ryo@desktop:~/tileQR_dashboard/inbox/manual' bash scripts/sync_results.sh scp
```

## CSV 形式

`results/benchmark/{host}[_ssrfb]_size{S}_nb{min}-{max}[_th{N}]_t{trials}_{timestamp}.csv`

- **tileqr**: 列 `threads, size, nb, ib, GFlops`（既存ダッシュボードと互換）
- **ssrfb**: 列 `threads, size, nb, ib, Time_sec, GFlops`

`GFlops` は `flops(nb, ib) / Time_sec / 1e9`。ssrfb の flops は
`flops_ssrfb(nb, ib) = 4*nb^3`（Buttari et al. arXiv:0709.1272 §3.2）。

失敗点（非ゼロ終了・パース失敗・タイムアウト）は欠測として記録しつつスイープを継続する
（`GFlops=NaN`、ssrfb は加えて `Time_sec=-1`）。欠測を暗黙に落とさない。

## ディレクトリ

```
src/plasma_perf/
  __main__.py            統一CLIディスパッチャ（bench / tune / viz）
  core/                  共有基盤（tileqr/ssrfb 共通）
    process.py           run_process / ProcResult（subprocess 実行）
    env.py               build_env（OMP/BLAS スレッド設定）
    csv_io.py            save_rows / build_output_path / save_raw_log / failure_reason
    timeout.py           estimate_timeout_sec
    sweep.py             run_sweep（nb×ib スイープ本体・CSV フラッシュ・欠測処理）
    paths.py             results / logs の標準パス
  benchmarks/
    base.py              Benchmark（抽象基底）/ PointResult
    cli.py               `bench {tileqr,ssrfb}` エントリ
    tileqr/runner.py     plasmatest dgeqrf を1点実行 / measure_gflops
    ssrfb/runner.py      NoFlush を1点実行 / measure_time / flops_ssrfb
  tuning/                Optuna チューニング（要 [tuning]）
    base.py              結果保存 / study 可視化
    normal_optuna.py     広域探索
    select_optuna.py     範囲を絞った探索
    ssrfb_optuna.py      NoFlush で最良 ib を決めてから dgeqrf を評価
    cli.py               `tune {normal,select,ssrfb,all}` エントリ
  viz/
    heatmap.py           nb×ib GFLOPS ヒートマップ
    cli.py               `viz heatmap` エントリ
scripts/
  run_campaign.sh        任意コマンドを tmux+ログで包む汎用ラッパー
  sync_results.sh        rclone / scp 転送
tests/
  test_core.py           パーサ・flops・スイープの単体テスト（実行ファイル不要）
results/benchmark/       計測CSV（gitignore）
logs/                    キャンペーンログ・失敗点の生ログ（gitignore）
```

## 設計上のポイント

- **計測とチューニングが同一の実行経路を通る**: Optuna の目的関数は
  `benchmarks/*/runner.py` の `measure_gflops` / `measure_time` を再利用する。
  スイープとチューニングで「1点の実行」がずれない。
- **tileqr / ssrfb の差は薄い**: コマンド生成・出力パース・flops 式・CSV 列だけ。
  スイープのループ本体（`core/sweep.py`）は1つで両方をまかなう。

## 既知の制約

- ssrfb は **N_IT=50000 が NoFlush(C++) 側ハードコード**のため、nb が大きいほど
  1点の所要時間が急増する。フルスイープ前に `--nb-max` を段階的に上げて確認すること。
- `--timeout-sec` 省略時の ssrfb タイムアウトは `flops_ssrfb` と `--assumed-gflops`
  からの粗い見積り。実測が大きく外れる場合は明示指定すること。
- 自動 resume（既存CSVを読んで計測済み点をスキップ）は未実装。

## テスト

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
