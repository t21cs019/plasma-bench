# 指示書：EPYC 7543 で OpenMP の待ち方をそろえて計測し、size 1024 のスイープを libgomp で取る（2026-11-25_omp-wait-ab）

あなた（研究室の EPYC 7543 の上の Claude Code）への指示書。devbox-lab の Claude が書いた。このディレクトリのスクリプトで次の2つを計測し、結果の確認までを行う。**結果の解釈（どの条件が速いか、なぜか）はしない。** 解釈は devbox-lab 側で行う。

- **A. 待ち方の計測**（`1_run.sh`、20〜40 分）：p = 32、6 点 × 4 条件、perf つき
- **B. size 1024 のスイープ**（`2_sweep_gomp.sh`、約 2 時間）：libgomp で nb 32〜512・ib の全格子を 5 回

全体で約 2 時間半。A を先に流し、A が終わってから B を流す（同時に流さない）。

## 1. 何をするのか（背景は最小限）

前回（`2026-11-24_libgomp-ab`）と同じ plasmatest・libplasma・OpenBLAS のまま、`plasmatest dgeqrf` を流す。OpenMP のランタイムは、libiomp5（いままでどおり）か、gompfwd.so を LD_PRELOAD して libgomp に替えたもの。

### A. 4 条件（rt.sh）

| 名前 | ランタイム | 待ち方 | 環境変数（ほかの待ち方の変数は unset） |
|---|---|---|---|
| idef | libiomp5 | 既定 | `KMP_AFFINITY=granularity=core,compact` |
| ibt0 | libiomp5 | すぐ眠る | 上に `KMP_BLOCKTIME=0` |
| gdef | libgomp（gompfwd.so を LD_PRELOAD） | 既定 | `OMP_PLACES=cores OMP_PROC_BIND=close` |
| gact | libgomp（同上） | 回り続ける | 上に `OMP_WAIT_POLICY=active` |

idef・gdef は前回の iomp・gomp と同じ条件。ほかのもの（plasmatest、libplasma、OpenBLAS 0.3.30.dev、ib、スレッド数）は変えない。

### B. size 1024 のスイープ

2026-10-27 に libiomp5 で取った size 1024 のスイープ（`plasma_perf bench tileqr --size 1024 --trials 5`）と同じ格子・同じ流し方で、ランタイムだけを gdef（libgomp、`OMP_PLACES=cores OMP_PROC_BIND=close`）にする。前と後に、idef と gdef を size 1024 の 5 点で 3 回ずつ流す「つなぎ」（bridge）が付く。

`scripts/hpc/lib/common.sh` の `hpc_set_cond` は、plasmatest の ldd に libiomp5 があると `KMP_AFFINITY` を選ぶので、libgomp では固定が効かない。`2_sweep_gomp.sh` は `hpc_set_cond` の固定を使わず、`rt.sh` の `rt_set gdef` で固定する。`common.sh` は直さない。

## 2. 守ること

- **共用機。** 流す前に、他のユーザーの CPU 使用率を確かめる（`ps -eo user:20,pcpu --sort=-pcpu | head`）。他のユーザーの合計が 100% を超えていたら流さず、ユーザーに伝える。計測中にほかの重い処理を同時に流さない
- **書き換えないもの：** `/usr/local`、`/opt`、`~/Library`、`~/Download/build_*`、`scripts/hpc/lib/`。sudo は使わない。パッケージを入れない
- **git：** このブランチ `feat/omp-wait-ab` 以外に push しない。main にマージしない。結果（`results/` の下）は git に入れない
- **スクリプトを直すとき：** 明らかな不具合だけ直してよい。直したらこのブランチに commit して push し、`REPORT.md`（§5）に書く。計測の条件（size、nb、ib、p、条件、種類、round の数、格子、trials、スレッドの固定、待ち方の変数、perf のイベントと周期）は変えない。変えたくなったら止めてユーザーに聞く。例外は §3.1 の 11 の「イベントが無いとき」だけ
- 迷ったら止める

## 3. 手順

```bash
cd ~/Workspace/plasma-bench
git fetch origin
git switch feat/omp-wait-ab              # 無ければ: git switch -c feat/omp-wait-ab --track origin/feat/omp-wait-ab
git pull --ff-only
S=scripts/hpc/epyc7543/campaigns/2026-11-25_omp-wait-ab
df -h .                                  # 空きが 5 GB 未満なら止める
```

### 3.1 preflight（3〜5 分。計測はしない）

```bash
MACHINE=epyc7543 bash $S/0_preflight.sh
```

`results/preflight_omp-wait-ab_<時刻>/log.txt` の各節が次のとおりかを確かめる。

| log.txt の節 | 期待 | 違ったら |
|---|---|---|
| 1. 他のジョブ | `other_users_cpu_pct` が 100 以下 | 止める |
| 2. 道具 | perf がある。`plasma_perf ok` | perf が無い、plasma_perf が読めないなら止める |
| 3. ライブラリ | `openblas_get_config` が `OpenBLAS 0.3.30.dev NO_AFFINITY USE_OPENMP ZEN MAX_THREADS=32`、libplasma が `/usr/local/lib` | OpenBLAS の版が違えば止める。ほかは記録して続ける |
| 4. gompfwd.so | `libgomp に無い 0 個` と `ok:` | 止める |
| 5. 結び付き | iomp は libgomp へ 0、gomp は libiomp5 へ 0（前回と同じ） | 止める |
| 6. 実行中のプロセス | 4 条件とも `threads=32`、`distinct_cpus=32`。environ に §1 の表の変数がそのとおりに出る | 止める |
| 7. 検算 | 4 条件とも `--test=y` が通る | 止める |
| 8. obshim | 4 条件とも `stamps` が 2 以上 | 止める |
| 11. perf のイベント | 4 つの `ls_dmnd_fills_from_sys.*` が「あり」で、符号がどれも `event=0x43` で umask が互いに違う。組 A・B の perf record の試しで、どのイベントにも試料があり、reduce_perfev が `in={…}` に全イベントを出す | 名前が無い・符号が読めない・record が失敗するなら、`PERF_B=generic` を付けて 0_preflight.sh をもう一度流し、通れば A は `PERF_B=generic` で流して REPORT に書く。組 A が失敗するなら止める |
| 12. 時分割 | perf stat の各行の 5 列目（数えた割合）が 100.00、`<not counted>`・`<not supported>` が無い | 100% 未満なら記録して続ける（REPORT に値を書く）。`<not supported>` なら止める |
| 13. 待ち方の変数 | ibt0 の `KMP_SETTINGS` の出力に `KMP_BLOCKTIME=0`、gact の `OMP_DISPLAY_ENV` の出力に `OMP_WAIT_POLICY = 'ACTIVE'`。idef は `KMP_BLOCKTIME=200`、gdef は `OMP_WAIT_POLICY = 'PASSIVE'`（未設定のときの libgomp の表示。実際は短く回ってから眠る） | 止める（条件が効いていない） |
| 14. スイープの試し | plasma_perf の 2 点に GFlops が出る。子の plasmatest の environ に `OMP_PLACES=cores`・`OMP_PROC_BIND=close`・`LD_PRELOAD=…gompfwd.so`、maps に gompfwd と libgomp、`distinct_cpus=32` | 止める（B は流さない。A は流してよい） |

11 の「perf script N 秒」も REPORT に書く（A の時間の見積もりに使う）。止める条件に当たったら、本計測は流さず、どの節がどう違ったかを log.txt の該当部分を添えてユーザーに伝える。

### 3.2 A. 待ち方の計測（tmux の中で。20〜40 分）

```bash
tmux new -d -s omp-wait-ab "cd ~/Workspace/plasma-bench && MACHINE=epyc7543 bash $S/1_run.sh 2>&1 | tee results/omp-wait-ab_console_\$(date +%Y%m%d_%H%M%S).txt"
```

（11 で `PERF_B=generic` にしたときは `MACHINE=epyc7543 PERF_B=generic bash $S/1_run.sh`）

- 既定の条件のまま流す：p = 32、size 4096 の nb 64・96・128・192 と size 2048 の nb 96・128（ib は `best_ib_epyc7543_p32.txt`）、4 条件、種類は stamp（5 round）・perfA（3 round）・perfB（3 round）。合計 264 回
- 他のユーザーが途中で重い処理を始めても、止めずに流しきる（各点の前の値が runs.csv の `other_cpu_before` に残る）。合計が長く 1000% を超えるなら、tmux のセッションを止めてユーザーに伝える
- 1 点が 900 秒で打ち切られることが続いたら、止めてユーザーに伝える
- 終わったら §3.4 の A の確認をしてから B に進む

### 3.3 B. size 1024 のスイープ（tmux の中で。約 2 時間）

A が終わり、他のユーザーの CPU 使用率が 100% 以下であることを確かめてから：

```bash
tmux new -d -s gomp-sweep1024 "cd ~/Workspace/plasma-bench && MACHINE=epyc7543 bash $S/2_sweep_gomp.sh 2>&1 | tee results/gomp-sweep1024_console_\$(date +%Y%m%d_%H%M%S).txt"
```

- 変数は付けない（size 1024、p = 32、nb 32〜512・ib 8〜nb/2 の 4 刻み、5 trials）
- 途中で他のユーザーが重い処理を始めても止めない（`other_cpu.csv` に 60 秒ごとに残る）。合計が長く 1000% を超えるなら止めてユーザーに伝える
- 3 時間を超えても終わらなければ、止めずにユーザーに伝える

### 3.4 終わったあとの確認

A：結果は `results/benchmark/epyc_s1_smt-off_omp-wait-ab_<時刻>/`

1. `runs.csv` の行数が 264（見出しを除く）。`exit` が 0 でない行と、`Time_sec` が空の行を数える
2. `run.log` の `[warn]` を数える
3. `perf/*_window.json` の `rel_err` の最大。0.01 を超えるものがあれば列挙する。`unparsed_lines` が 0 でないものの数
4. `perf/*_events.csv` が perfA・perfB で各 72 個あり、空でない
5. `perf/*.rec.txt` に `lost` を含むものの数
6. `affinity_*_p32.txt` の 4 条件の `threads=` と `distinct_cpus=`
7. `other_cpu_before` の最大と、10% を超えた点の数
8. 開始・終了の時刻と、ディレクトリの大きさ（`du -sh`）

B：結果は `results/benchmark/epyc_s1_smt-off_gomp-sweep1024_<時刻>/`

1. `sweep.csv` の行数（見出しを除く）が、`sweep_console.txt` の `points/trial` × 5 と一致するか。`欠測` の行の数
2. `bridge.csv` が 60 行（before・after 各 30）で、`exit` がすべて 0、`GFlops` が空でない
3. `affinity_gdef_p32.txt` の `threads=` と `distinct_cpus=`
4. `other_cpu.csv` の `other_cpu` の最大と、10% を超えた行の数
5. `meta.txt` の `sweep end` の秒数

数字は写すだけにして、良し悪しの判断や解釈はしない。

## 4. ユーザーに伝えること（最後に）

- preflight の表の各行が期待どおりだったか（違った行は中身も）。11 でイベントを替えたかどうか。11 の perf script の秒数
- A・B それぞれの始まりと終わりの時刻、§3.4 の結果
- 直したスクリプトがあれば、その commit
- **持ち帰るもの**（ユーザーが手で devbox-lab の `~/Download/epyc/` にコピーする）：
  - `results/preflight_omp-wait-ab_<時刻>/`
  - `results/benchmark/epyc_s1_smt-off_omp-wait-ab_<時刻>/`
  - `results/benchmark/epyc_s1_smt-off_gomp-sweep1024_<時刻>/`
  - `results/omp-wait-ab_console_<時刻>.txt`、`results/gomp-sweep1024_console_<時刻>.txt`
- 持ち帰ったら devbox-lab で解析する（A は `~/Workspace/tileQR_data/analysis/2026-11-25_omp-runtime-followup/RESULT.md` §4、B は 10-27 の libiomp5 の size 1024 と比べる）、と添える

## 5. REPORT.md

§4 と同じ内容を `results/benchmark/epyc_s1_smt-off_omp-wait-ab_<時刻>/REPORT.md` にも書く（preflight だけで止めたときは `results/preflight_omp-wait-ab_<時刻>/REPORT.md`）。

## 6. 参考

- 前回の計測：`scripts/hpc/epyc7543/campaigns/2026-11-24_libgomp-ab/`（TASK.md・スクリプト）。今回の `rt.sh`・`0_preflight.sh`・`1_run.sh` はそこから写して、待ち方の条件と perf の組 A・B を足したもの。`mk_gompfwd.sh`・`shim/obshim.c`・`best_ib_epyc7543_p32.txt` は同じもの
- perf の組（`perfev.sh`。周期はイベントごとに項 `period=` で固定。計時区間で切るのは前回と同じ。集計は `reduce_perfev.py`）
  - 組 A：`task-clock`（100000 ns ＝ 100 µs）、`cycles`（1000003）、`instructions`（1000003）
  - 組 B：`cycles`（1000003）、`ls_dmnd_fills_from_sys.{lcl_l2,int_cache,ext_cache_local,mem_io_local}`（10007。perf list の event・umask で `cpu/…/` の形にして渡す）
- B の比べる相手：tileQR_data の `raw/qr_sweep/epyc_s1_smt-off/epyc_size1024_t32_nb32-512_20261027-080944_r{1..5}.csv`
