# 指示書: チューニング手法の実測比較（tileQR_bench への追加）

## 0. この指示書の位置づけ

`tileQR_bench` に **チューニング手法そのものを実行して時間と性能を測る**機能を追加する。
既存の `benchmark.py`（nb×ib 全スイープ）とは目的が違う。スイープは「正解表を作る」ためのもので、
こちらは「**正解を知らない状態から、各手法が何秒で何 GFlops に到達するか**」を測る。

配置先: `tileQR_bench/tuning/`（新規）
最初の対象機: **i3-10100**（卒論 T21CS019 と同一機種のため直接比較できる）

### 比較する3手法

| ID | 手法 | 由来 |
|---|---|---|
| `agullo` | dtsmqr ベンチ → MaxIB ペア → ヒューリスティック2 で PS 8組 → QRベンチ | 卒論の「既存手法」(Agullo LAWN 242) |
| `optuna_full` | nb∈[4,512] 偶数, ib∈[2,nb/2] 偶数 を Optuna 100試行（各5回計測） | 卒論の「Optuna」 |
| `model` | SSRFB で β 推定 → モデルが帯を返す → その帯で Optuna 16試行（各1回）→ 上位3を3回検証 | **本研究の提案手法** |

`agullo` と `optuna_full` は**卒論の再現**なので、卒論の設定を変えないこと。
差が出た場合に「実装が違うから」と言い訳できなくなる。

---

## 1. 絶対に守ること（これを破ると実験が無意味になる）

### 1.1 時間計測に全ての前処理を含める

各手法の「チューニング時間」は、**何も知らない状態から最終的な (nb, ib) を1組出すまでの実時間**。

- `agullo`: dtsmqr ベンチマーク時間を**含む**
- `model`: **SSRFB による β 推定の時間を含む**
- `optuna_full`: 探索時間のみ（前処理なし）

β 計測を時間に含めないと提案手法が不当に有利になる。**必ず含める**。

ただし β は機種ごとに1回でよく全サイズで使い回せるので、
出力には `time_total_sec` と `time_beta_sec` を**別々に記録**し、
後から「2サイズ目以降のコスト」を計算できるようにする。

### 1.2 性能の評価は「正解表」との突き合わせで行う

各手法が返した (nb, ib) の性能は、その場の計測値ではなく
**`tileQR_data` の既存スイープ（正解表）の平均値**で評価する。

理由: その場の1回計測にはノイズが乗る。「手法が良い pair を選べたか」を測りたいのであって
「たまたま速く回ったか」ではない。

- 正解表は `tileQR_data/derived/qr_sweep.parquet` の
  `config=i3-10100_s1_smt-off, size=<SIZE>` を使う
- 評価は**実験後にオフラインで行う**。実行時は (nb, ib) と時間だけ記録すればよい
- 正解表に無い (nb, ib) が返された場合は、最近傍の nb を使い、その旨をフラグで残す
  （**推定で埋めない**。フラグを立てて後で判断する）

### 1.3 各手法を同じ条件で複数回まわす

卒論は10回試行している。**最低5回、できれば10回**。
Optuna は seed で結果が変わるので、`--repeat` で繰り返し、seed を `0..repeat-1` で振る。

### 1.4 手法間で CPU 状態を揃える

- 3手法を**同一セッション内で逐次実行**する（並行実行禁止）
- 各手法の実行前に 30秒スリープを入れて熱を落ち着かせる（`--cooldown 30`）
- `taskset` でピニングする。SMT 状態は実行前にスナップショットを取る（1.5）

### 1.5 プロベナンス記録

実行開始時に以下を出力先に保存する。これが無いと後から条件が復元できない。

- `lscpu` の出力
- `lscpu -p` から求めた SMT 有効/無効（`smt_active.txt`）
- `nproc`、`OMP_NUM_THREADS`
- `git rev-parse HEAD`（tileQR_bench 側）
- 使用した β の値と、その推定に使った SSRFB の生データ

---

## 2. ディレクトリ構成

```
tileQR_bench/
  tuning/
    __init__.py
    run_tuning.py          # エントリポイント（CLI）
    methods/
      __init__.py
      agullo.py            # 卒論の既存手法
      optuna_full.py       # 卒論の Optuna
      model.py             # 提案手法
    model_predict.py       # モデル式（帯と点を返す。依存なし・純関数）
    beta_fit.py            # SSRFB 計測 → β 推定
    kernel_bench.py        # dtsmqr / ssrfb カーネル計測ラッパー
  scripts/
    run_tuning_campaign.sh # tmux で3手法×N回を逐次実行
  specs/
    tuning_comparison/
      README.md            # この指示書の要約と実行手順
  results/
    tuning/                # 出力CSV
```

既存ファイルは**変更しない**。`src/run_time_dgeqrf.py` は再利用する（import する）。

---

## 3. 実装仕様

### 3.1 `tuning/model_predict.py`

**依存なしの純関数**にすること（numpy のみ可）。これがモデルの正典実装になる。

```python
S0 = 4096.0
C2, M, DELTA, K0 = 3.43, 0.19, 0.836, 2.3   # クリーン領域2定数版
THR = 0.92

def perf_shape(nb, beta, p, size):
    """E(nb)·η(nb)。A は不要（比しか使わないため）"""
    x = C2 * p**M * nb / (S0 * (size / S0)**DELTA)
    return nb / (nb + beta) / (1.0 + x**K0)

def predict_nb(beta, p, size):
    """モデルが返す最適 nb（1点）"""

def predict_band(beta, p, size, thr=THR):
    """探索範囲 (lo, hi)。perf_shape が最大値の thr 倍以上になる nb の範囲"""
```

- nb は 8〜2048 の整数グリッドで評価してよい（連続最適化は不要）
- 定数はモジュール先頭に定義し、**由来のコメントを必ず書く**
  （`# tileQR_data 55条件・LOMO検証済み。詳細は tileQR_research の 2026-09-01_モデル検証まとめ`）
- 定数を関数の引数で上書きできるようにしておく（将来の再フィット用）

### 3.2 `tuning/beta_fit.py`

SSRFB を計測して β を推定する。

- 計測点: **nb ∈ {64, 128, 256, 384, 512}、各1回、ib ≤ 64**
- ただしサーバ機向けに `--beta-reps` で回数を変えられるようにする（既定1、サーバは3推奨）
- 各 nb について、計測した ib のうち**最大の GFlops** を採用（MaxIB と同じ考え方）
- `GFlops = G_inf · nb/(nb+β)` を最小二乗フィットして β を返す
  - フィットは scipy を使わず**自前の1次元探索**で書く（β を対数グリッドで走査 → 各 β で G_inf は解析解）
  - 理由: 計測コアを標準ライブラリのみで動かす既存方針を壊さないため。
    scipy を入れるくらいなら 200行のグリッド探索の方が移植性が高い
- 生の計測値も CSV に残す（`results/tuning/beta_<host>_<ts>.csv`）
- **計測にかかった実時間を返す**（1.1 のため）

SSRFB の呼び出しは既存の計測経路に合わせる。`plasmatest` で ssrfb 相当が叩けない場合は、
`src/run_time_dgeqrf.py` と同じ書き方で `kernel_bench.py` にラッパーを作る。
**叩き方が不明な場合は、実装を進めずに質問すること**（適当なコマンドをでっち上げない）。

### 3.3 `tuning/methods/agullo.py`

卒論2章の手順をそのまま実装する。

1. dtsmqr を nb×ib で計測（**卒論は50回実行して平均**。`--kernel-reps` 既定50）
   - nb, ib の範囲は卒論に合わせる。不明なら `nb ∈ [4,512] step 4`, `ib ∈ [2, nb/2] step 2`
2. 各 nb について最高性能の ib を選ぶ → **MaxIB ペア**
3. **ヒューリスティック2**で PS ペアを8組選ぶ。3条件すべてを満たすもの:
   1. MaxIB ペアが作る nb–性能空間の**凸包上**にあるペア
   2. 凸包上で**傾きが最も大きい**ペア
   3. **nb 軸を8等分**した各領域から1つずつ選ぶ
4. 8組それぞれで QR ベンチを実行し、最良を返す

凸包は `scipy.spatial` を使わず、**単調鎖法（monotone chain）を自前で書く**（上側凸包のみ必要）。
条件2と3の解釈は卒論・Agullo 原論文に依存するため、
**実装した解釈をコード内 docstring に明記**し、曖昧な点は結果CSVの `notes` 列に残す。

### 3.4 `tuning/methods/optuna_full.py`

- `nb`: 4〜512 の**偶数**、`ib`: 2〜nb/2 の**偶数**
- 100試行、**1試行につきQRを5回計測してその平均**を目的値とする（卒論通り）
- Optuna の TPESampler、seed は引数で受ける
- optuna が入っていない環境では明確なエラーを出す（`pip install optuna` を案内）

### 3.5 `tuning/methods/model.py`

1. β を取得（`beta_fit.py`。`--beta` で外部から与えられた場合は計測をスキップし、時間0とする）
2. `predict_band(beta, p, size)` で帯 (lo, hi) を得る
3. Optuna で探索。**16試行、1試行につきQRは1回計測**
   - `nb ∈ [lo, hi]`、`ib ∈ [max(8, nb/8), nb/2]`
   - **warm start**: 最初の3試行を `predict_nb` の値と、その 0.75倍・1.3倍で埋める
     （`study.enqueue_trial`。ib は nb/4 に最も近い候補）
4. 観測上位3ペアを**各3回**再計測し、平均が最良のものを最終回答とする

`p` は**物理コア数**を使う。`lscpu -p=CORE` のユニーク数から求める（`nproc` ではない）。

### 3.6 `tuning/run_tuning.py`（CLI）

```bash
python tuning/run_tuning.py \
    --size 4096 \
    --methods agullo,optuna_full,model \
    --repeat 10 \
    --threads 4 \
    --cooldown 30 \
    --output results/tuning/
```

出力 CSV: `results/tuning/tuning_<host>_size<S>_th<N>_<YYYYmmdd-HHMMSS>.csv`

列:

```
host, size, threads, physical_cores, smt_active, method, run_id, seed,
nb, ib, time_total_sec, time_prep_sec, n_qr_exec, n_kernel_exec,
beta, band_lo, band_hi, gflops_observed, notes
```

- `time_prep_sec`: agullo は dtsmqr ベンチ時間、model は β 計測時間、optuna_full は 0
- `n_qr_exec` / `n_kernel_exec`: **実行回数を必ず数えて記録する**（時間の内訳検証に使う）
- `gflops_observed`: その場での観測値。**評価には使わない**が記録は残す

### 3.7 `scripts/run_tuning_campaign.sh`

既存の `run_campaign.sh` と同じ作法で書く。

- tmux セッション内で実行（SSH 切断耐性）
- ログを `logs/tuning_<host>_size<S>_<ts>.log` に tee
- 既存セッション名が使われていたらエラーで停止
- 環境変数: `SIZE`, `SIZES`(複数逐次), `REPEAT`, `THREADS`, `METHODS`, `SESSION`, `COOLDOWN`

---

## 4. 評価スクリプト（オフライン）

`tuning/evaluate.py` を作る。実行環境ではなく**手元（データがある側）で動かす**。

- 入力: `results/tuning/*.csv` と `tileQR_data/derived/qr_sweep.parquet`
- 各行の (nb, ib) を正解表と突き合わせ、`gflops_true`（平均値）と `ratio = gflops_true / peak` を算出
- 手法ごとに集計: `ratio` の平均・最小・標準偏差、`time_total_sec` の平均
- 卒論 表5.1 と同じ形式の表を出力する

**注意**: 正解表は SMT-off・4スレッドのもの。実測を SMT-on で行った場合は突き合わせできない。
実行時の `smt_active` と正解表の条件が食い違ったら**エラーで止める**（黙って比較しない）。

---

## 5. まず i3-10100 でやること（実行手順）

```bash
# 0. SMT 状態を確認（正解表が SMT-off なので off に揃える）
lscpu | grep -i "Thread(s) per core"

# 1. 短縮版で動作確認（repeat=1, agullo の kernel-reps を 5 に落とす）
python tuning/run_tuning.py --size 4096 --methods model --repeat 1 --threads 4
python tuning/run_tuning.py --size 4096 --methods agullo --repeat 1 --threads 4 --kernel-reps 5

# 2. 本番
SIZE=4096 REPEAT=10 THREADS=4 bash scripts/run_tuning_campaign.sh
```

**size=4096 が最優先**（卒論と直接比較できる唯一のサイズ）。
これが終わったら `SIZES="1024 2048"` で追加する。1024/2048 は卒論に対応データが無いが、
**「卒論のキャッシュ式（nb 120–210 固定）は size が変わると外れる」ことを示す証拠**になるので価値が高い。

### 所要時間の目安（i3-10100, size=4096, 1回あたり）

- `model`: β計測 約2分 + QR 25回 ≒ **3分**
- `optuna_full`: QR 500回 ≒ **10〜20分**
- `agullo`: dtsmqr ベンチが支配的。**卒論では 4878s（約80分）** → repeat=10 は非現実的

→ **`agullo` だけ `--repeat 3` に落としてよい**。その場合 CSV にその旨を残す。
実行前に必ず `--dry-run` で見積もりを表示する機能を付けること。

---

## 6. 実装上の注意

- **既存の `src/` を壊さない**。`benchmark.py` / `run_time_dgeqrf.py` は import して使うだけ
- optuna は `tuning/` でのみ使う。計測コア（`src/`）は標準ライブラリのみの方針を維持
- `tuning/` 用の依存は `tuning/requirements.txt` に分離（optuna, numpy のみ）
- 途中クラッシュに備え、**1 run 終わるごとに CSV へ追記**する（既存 benchmark.py と同じ作法）
- 各手法は**同じインタフェース**にする:
  `run(size, threads, seed, ctx) -> dict`（nb, ib, time_total_sec, time_prep_sec, n_qr_exec, ...）
- `--dry-run` で「各手法が QR を何回・カーネルを何回実行するか」と推定時間を表示して終了

---

## 7. 完了条件

- [ ] `python tuning/run_tuning.py --dry-run --size 4096 --methods agullo,optuna_full,model` が
      実行回数と推定時間を表示する
- [ ] 3手法とも `--repeat 1` で完走し、CSV に1行ずつ出る
- [ ] CSV に `time_prep_sec` と `n_qr_exec` が正しく入っている
- [ ] `lscpu` と `smt_active.txt` が出力先に保存されている
- [ ] `tuning/evaluate.py` が卒論 表5.1 形式の表を出す
- [ ] `specs/tuning_comparison/README.md` に実行手順が書かれている

---

## 8. 判断に迷ったら質問すること

以下は**勝手に決めずに聞く**:

- SSRFB / dtsmqr カーネルの叩き方が `plasmatest` で不明な場合
- ヒューリスティック2の条件2・3の解釈が複数ありうる場合
- 卒論の dtsmqr ベンチの nb/ib 範囲が特定できない場合

適当な実装で埋めると「卒論の再現」ではなくなり、比較の意味が失われる。
