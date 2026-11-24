# 指示書：EPYC 7543 で OpenMP のランタイムを替えて計測する（2026-11-24_libgomp-ab）

あなた（研究室の EPYC 7543 の上の Claude Code）への指示書。devbox-lab の Claude が書いた。このディレクトリのスクリプトを流して計測し、結果の確認までを行う。**結果の解釈（どちらのランタイムが速いか、なぜか）はしない。** 解釈は devbox-lab 側で行う。

## 1. 何をするのか（背景は最小限）

PLASMA のタイル QR 分解（`plasmatest dgeqrf`）を p = 32 で流し、OpenMP のランタイムだけを替えて Time と perf の内訳を比べる。

- **iomp**：いままでと同じ。plasmatest と `/usr/local/lib` の libplasma は Intel の OpenMP（libiomp5）に直接リンクされている
- **gomp**：同じバイナリのまま、中継ライブラリ `gompfwd.so`（`mk_gompfwd.sh` が作る）を LD_PRELOAD する。`GOMP_*`・`omp_*` が GNU の libgomp に結び付く。libplasma は libiomp5 の版（`VERSION`）を名指しで参照しているので、`LD_PRELOAD=libgomp.so.1` だけでは替わらない。そのために中継を挟む

ほかのもの（plasmatest、libplasma、OpenBLAS 0.3.30.dev、ib、スレッド数）は変えない。

## 2. 守ること

- **共用機。** 流す前に、他のユーザーの CPU 使用率を確かめる（`ps -eo user:20,pcpu --sort=-pcpu | head`）。他のユーザーの合計が 100% を超えていたら流さず、ユーザーに伝える。計測中にほかの重い処理（ビルド、テスト、別の計測）を同時に流さない
- **書き換えないもの：** `/usr/local`、`/opt`、`~/Library`（自前ビルドのライブラリ）、`~/Download/build_*`。sudo は使わない。パッケージを入れない
- **git：** このブランチ `feat/libgomp-ab` 以外に push しない。main にマージしない。結果（`results/` の下）は git に入れない（`.gitignore` 済み。結果はユーザーが手で devbox-lab に持ち帰る）
- **スクリプトを直すとき：** 明らかな不具合（文法の誤り、パスの間違い、この計算機の道具の違いで止まる）だけ直してよい。直したら、このブランチに commit して push し、何をなぜ直したかを `REPORT.md`（§5）に書く。計測の条件（size、nb、ib、p、種類、round の数、スレッドの固定、待ち方）は変えない。変えたくなったら止めてユーザーに聞く
- 止まってユーザーに聞く場面は §3 の「止める条件」に書いた。迷ったら止める

## 3. 手順

作業の場所は plasma-bench の直下（`~/Workspace/plasma-bench`。11-24 の計測もここで流した）。

```bash
cd ~/Workspace/plasma-bench
git fetch origin
git switch feat/libgomp-ab        # 無ければ: git switch -c feat/libgomp-ab --track origin/feat/libgomp-ab
git pull --ff-only
S=scripts/hpc/epyc7543/campaigns/2026-11-24_libgomp-ab
df -h .                           # 空きが 5 GB 未満なら止める（perf の出力を残す）
```

### 3.1 preflight（2〜4 分。計測はしない）

```bash
MACHINE=epyc7543 bash $S/0_preflight.sh
```

`results/preflight_libgomp-ab_<時刻>/log.txt` ができる。次の表のとおりかを確かめる。

| log.txt の節 | 期待 | 違ったら |
|---|---|---|
| 1. 他のジョブ | `other_users_cpu_pct` が 100 以下 | 止める |
| 3. ライブラリ | `openblas_get_config` が `OpenBLAS 0.3.30.dev NO_AFFINITY USE_OPENMP ZEN MAX_THREADS=32`。libplasma が `/usr/local/lib`、libiomp5 と libgomp の両方が ldd にある | 記録して続ける（REPORT に書く）。OpenBLAS の版が違えば止める |
| 4. gompfwd.so | `libgomp に無い 0 個` と `ok:` | 0 個でなければ止める（gomp の条件が libiomp5 と混ざる） |
| 5. 結び付き：iomp | plasma・OpenBLAS・plasmatest から **libgomp に結び付いた名前** が空（OpenBLAS の `omp_*` が libgomp に行っていたら、それも事実として記録する。止めない） | 記録して続ける |
| 5. 結び付き：gompnaive | libplasma の `GOMP_*` が **libiomp5 のまま**（中継が要る理由。こうなるのが想定どおり） | 記録して続ける |
| 5. 結び付き：gomp | plasma・OpenBLAS・plasmatest から **libiomp5 に結び付いた名前** が空。結び付き先は `gompfwd.so` | 空でなければ止める |
| 6. 実行中のプロセス | iomp・gomp とも `threads=32`、`distinct_cpus=32` | threads が 32 を超える（両方のランタイムがスレッドを作った）、または distinct_cpus が 32 未満（固定が効いていない）なら止める |
| 7. 検算 | iomp・gomp とも `--test=y` の結果が通っている（失敗の表示が無い） | gomp で失敗したら止める |
| 8. obshim | iomp・gomp とも `stamps` が 2 以上、`n_alloc` が 0 でない | 止める |
| 9. libgomp の機械語 | `libgomp.dis.gz` ができている。debug のシンボルは取れなくてもよい | 記録して続ける |
| 2'. perf --call-graph | `perf record exit=0`、枠の行が 0 でない | 記録して続ける（本計測の perfg だけが使えない。`PERFG=""` にはしない。そのまま流す） |
| 10. 自前ビルド | 記録だけ | — |

- 9 の debuginfod は、libgomp の build-id を `debuginfod.ubuntu.com` に問い合わせる（送るのは build-id だけ）。60 秒で打ち切る
- **止める条件に当たったら**、本計測は流さずに、どの節がどう違ったかを log.txt の該当部分を添えてユーザーに伝える

### 3.2 本計測（tmux の中で。30〜60 分の見込み。gomp の nb 32 が遅ければ延びる）

```bash
tmux new -d -s libgomp-ab "cd ~/Workspace/plasma-bench && MACHINE=epyc7543 bash $S/1_run.sh 2>&1 | tee results/libgomp-ab_console_\$(date +%Y%m%d_%H%M%S).txt"
```

- 既定の条件のまま流す（p = 32、size 1024・2048・4096、nb 32・64・96・128・192、stamp・pass・perf × iomp・gomp × 3 round、そのあと gomp の size 4096・nb 32・64・128 で perf `--call-graph`）。環境変数で条件を変えない
- 1 点ごとに `r1 gomp perf p32 size 4096 nb 64 ...` の行が出る。ときどき進み具合と他のユーザーの CPU を見る（`tmux capture-pane -pt libgomp-ab | tail`）。短い間隔で見張り続けなくてよい
- 他のユーザーが途中で重い処理を始めても、止めずに流しきる（各点の前の値が runs.csv の `other_cpu_before` に残る。重なった点は devbox-lab で除く）。ただし他のユーザーの合計が長く 1000% を超えるなら、tmux のセッションを止めてユーザーに伝える
- 1 点が 900 秒で打ち切られる（`[warn] 終了コード 124`）ことが続いたら、止めてユーザーに伝える

### 3.3 終わったあとの確認

結果は `results/benchmark/epyc_s1_smt-off_libgomp-ab_<時刻>/`。

1. `runs.csv` の行数が 273（見出しを除く。270 ＋ perfg 3）。`exit` が 0 でない行と、`Time_sec` が空の行を数える
2. `run.log` の `[warn]` を数える（reduce の失敗、終了コード）
3. `perf/*_window.json` の `rel_err`（perf の区間と plasmatest の Time のずれ）の最大。0.01（1%）を超えるものがあれば列挙する
4. `perf/perfg_gomp_*_chains.csv` が 3 つあり、空でない
5. `affinity_iomp_p32.txt`・`affinity_gomp_p32.txt` の `threads=` と `distinct_cpus=`
6. `other_cpu_before` の最大と、10% を超えた点の数
7. 各ディレクトリの大きさ（`du -sh`）

数字は写すだけにして、良し悪しの判断や解釈はしない。

## 4. ユーザーに伝えること（最後に）

- preflight の表の各行が期待どおりだったか（違った行があれば、その中身）
- 本計測の始まりと終わりの時刻、§3.3 の 1〜7 の結果
- 直したスクリプトがあれば、その commit
- **持ち帰るもの**（ユーザーが手で devbox-lab の `~/Download/epyc/` にコピーする）：
  - `~/Workspace/plasma-bench/results/preflight_libgomp-ab_<時刻>/`
  - `~/Workspace/plasma-bench/results/benchmark/epyc_s1_smt-off_libgomp-ab_<時刻>/`
  - `~/Workspace/plasma-bench/results/libgomp-ab_console_<時刻>.txt`
- 持ち帰ったら、devbox-lab のチャットで `/pickup ~/Documents/tileQR_research/chats/2026-11-24_epyc-libgomp_brief.md` の続きとして解析する、と添える

## 5. REPORT.md

§4 と同じ内容を `results/benchmark/epyc_s1_smt-off_libgomp-ab_<時刻>/REPORT.md` にも書く（preflight だけで止めたときは `results/preflight_libgomp-ab_<時刻>/REPORT.md`）。持ち帰ったときに devbox-lab で読む。

## 6. 参考

- 計測と解析の正本（devbox-lab）：`~/Workspace/tileQR_data/analysis/2026-11-24_epyc-libgomp/`（INSTRUCTIONS.md・RESULT.md・scripts/）。このディレクトリはそこから写したもの
- 同じ形の前の計測：`results/benchmark/epyc_s1_smt-off_tau-cause_20261124_*`（11-24。obshim・perf の使い方は同じ）
- スクリプトの役割

| ファイル | 役割 |
|---|---|
| `0_preflight.sh` | 計測の前の確認（ライブラリ・結び付き・スレッド・検算・libgomp の機械語） |
| `1_run.sh` | 本計測（ランタイムを点ごとに交互に） |
| `rt.sh` | ランタイムの条件（LD_PRELOAD・スレッドの固定）、実行中のプロセスの確認 |
| `mk_gompfwd.sh` | 中継ライブラリ gompfwd.so を `gompfwd/` に作る |
| `shim/obshim.c` | 計時区間の時刻、OpenBLAS の作業領域の確保の回数と時間（`shim/obshim.so` に作る） |
| `reduce_perf.py`・`perfmap.py`・`reduce_perfg.py` | perf の出力を計時区間で切って数える |
| `best_ib_epyc7543_p32.txt` | 各 size・nb の ib |
