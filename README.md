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
# 3b. チューニング・可視化を使う場合のみ（uv + uv.lock で再現）:
bash scripts/setup_python.sh          # uv 導入 + uv sync --extra tuning --extra viz
```

> チューニング/可視化は uv の仮想環境（`.venv`）で動かす。実行は `uv run python -m ...`。
> 計測（bench）は標準ライブラリのみなので uv 無しの `python3 -m ...` でも動く。

## 統一CLI

すべて `python -m plasma_perf <command>` で呼ぶ（`src/` を PYTHONPATH に載せる）。

```bash
# --- 計測（bench）: 標準ライブラリのみ。uv 無しでも動く ---
export PYTHONPATH=src
python3 -m plasma_perf bench tileqr --size 4096 --trials 5
python3 -m plasma_perf bench ssrfb  --exe /path/to/NoFlush --trials 5
# ssrfb: MAT_SIZE=8192, N_IT=200 を指定
python3 -m plasma_perf bench ssrfb  --exe /path/to/NoFlush --size 8192 --n-it 200 --trials 5

# --- チューニング（tune）/ 可視化（viz）: uv の venv で実行 ---
uv run python -m plasma_perf tune ssrfb  --counts 5 --trials 20
uv run python -m plasma_perf tune select --nb-min 192 --nb-max 336 --counts 10 --trials 50
uv run python -m plasma_perf viz heatmap --input results/benchmark/xxx.csv --threads 16
```

本番計測・チューニングは tmux ラッパー経由で回すと SSH 切断に耐える（後述）。
`scripts/run_campaign.sh` は任意コマンドを包むので、`-- uv run python -m ...` も渡せる。

### bench のオプション（`plasma_perf bench {tileqr,ssrfb}`）

| オプション | 説明 | 既定値 |
|---|---|---|
| `target` | `tileqr`（フルQR）/ `ssrfb`（カーネル単体）| 必須 |
| `--exe` | 実行ファイル。tileqr=plasmatest（既定は環境変数 `PLASMA_TEST`）、ssrfb=NoFlush（既定は `NOFLUSH_PATH`）| 環境変数 |
| `--threads` | OMP_NUM_THREADS。明示時はファイル名に `th<N>` を付与 | tileqr=全コア / ssrfb=1 |
| `--size` | 行列サイズ。tileqr=正方行列の次元 / ssrfb=NoFlush の MAT_SIZE（キャッシュ占有量）| `4096` |
| `--n-it` | **ssrfb 専用**: NoFlush の反復回数 N_IT（tileqr では無視）| `50` |
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
SESSION=tune scripts/run_campaign.sh -- uv run python -m plasma_perf tune ssrfb --counts 5 --trials 20

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

失敗点（非ゼロ終了・パース失敗・タイムアウト）は欠測として記録しつつスイープを継続する。
欠測値の規約は benchmark ごとに異なる:

- **tileqr**: `GFlops=0.0`（旧 tileQR_bench との CSV 互換）
- **ssrfb**: `Time_sec=-1`, `GFlops=NaN`

いずれもコンソールには「欠測」と表示され、欠測点数として集計される（暗黙に落とさない）。

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
  setup_python.sh        uv で Python 環境（tuning/viz）を構築（uv.lock 固定）
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

## SSRFB カーネル（NoFlush）について

- NoFlush のソースは [`plasma-workspace`](https://github.com/t21cs019/plasma-workspace)
  の `tune_ssrfb/` に同梱・ビルドされる。`MAT_SIZE`（`--size`）と `N_IT`（`--n-it`）は
  実行時引数で指定できる（既定 4096 / 50）。
- `N_IT` を大きくすると平均が安定する一方 1 点の所要時間が増える。`nb` が大きい領域ほど
  1 点が重くなるので、フルスイープ前に `--nb-max` を段階的に上げて確認すること。

## 既知の制約

- `--timeout-sec` 省略時の ssrfb タイムアウトは `flops_ssrfb`・`--n-it`・`--assumed-gflops`
  からの粗い見積り。実測が大きく外れる場合は明示指定すること。
- 自動 resume（既存CSVを読んで計測済み点をスキップ）は未実装。

## テスト

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
