#!/usr/bin/env bash
# =============================================================================
# 0_preflight.sh — omp-wait-ab（1_run.sh）と size 1024 の libgomp のスイープ（2_sweep_gomp.sh）の前の確認
#                  （EPYC 7543。計測はしない、3〜5 分）
#
#   1〜8 は 2026-11-24_libgomp-ab の preflight と同じ確認（6〜8 は 4 条件に広げた）。9・10 は今回は無い。
#   1. 他のジョブ（共用機）
#   2. 道具
#   3. ライブラリ（パス・md5・NEEDED・openblas_get_config・libgomp と libiomp5 の版）
#   4. gompfwd.so を作る（mk_gompfwd.sh）。中継できない名前（libgomp に無いもの）があれば表示
#   5. 結び付き（LD_DEBUG=bindings）：iomp・gompnaive・gomp の3通り
#   6. 実行中のプロセス（p = 32）：4 条件のスレッドの数・CPU・maps・environ
#   7. 検算：4 条件で size 1024 を --test=y
#   8. obshim（stamp）：4 条件で計時の時刻が取れるか
#  11. perf のイベント：Zen3 の取り寄せ先の名前と符号、組 A・B で perf record が通り、どのイベントにも試料があるか
#  12. 時分割：組 A・B を perf stat で数え、<not counted>・<not supported> が無く、数えた割合が 100% か
#  13. 待ち方の変数：KMP_SETTINGS=1（idef・ibt0）と OMP_DISPLAY_ENV=true（gdef・gact）の出力
#  14. size 1024 のスイープ（2_sweep_gomp.sh）の試し：gdef で plasma_perf を 2 点だけ流し、固定と LD_PRELOAD が plasmatest に届くか
#
#   使い方（plasma-bench の直下で。ブランチ feat/omp-wait-ab。S=scripts/hpc/epyc7543/campaigns/2026-11-25_omp-wait-ab）:
#       MACHINE=epyc7543 bash $S/0_preflight.sh
#   出力：results/preflight_omp-wait-ab_<時刻>/log.txt。ディレクトリごと devbox-lab の ~/Download/epyc/ に持ち帰る。
# =============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PB="${PLASMA_BENCH:-$PWD}"
MACHINE=${MACHINE:?MACHINE=epyc7543 を付ける}
export MACHINE
OUT="${PB}/results/preflight_omp-wait-ab_$(date +%Y%m%d_%H%M%S)"   # results/ は git の管理外
mkdir -p "${OUT}"
exec > >(tee "${OUT}/log.txt") 2>&1
echo "# preflight（omp-wait-ab・size 1024 の libgomp のスイープ）$(date '+%F %T') host=$(hostname) user=$(id -un) kernel=$(uname -r) MACHINE=${MACHINE}"
echo "# 出力: ${OUT}"
echo "# git: $(git -C "${PB}" rev-parse --abbrev-ref HEAD) $(git -C "${PB}" rev-parse --short HEAD)"

# shellcheck source=/dev/null
source "${PB}/scripts/hpc/lib/common.sh"
hpc_setup
# shellcheck source=/dev/null
source "${HERE}/rt.sh"
# shellcheck source=/dev/null
source "${HERE}/perfev.sh"
GOMPFWD="${HERE}/gompfwd/gompfwd.so"
SO="${HERE}/shim/obshim.so"
hpc_select_nodes 32
hpc_set_cond close bind

echo; echo "## 1. 他のジョブ"
uptime
ps -eo user:20,pcpu,etime,args --sort=-pcpu | head -6 | cut -c1-160
echo "other_users_cpu_pct=$(ps -eo user:20,pcpu | awk -v me="$(id -un)" 'NR>1 && $1!=me {s+=$2} END{printf "%.0f", s}')"
df -h "${PB}" | tail -1

echo; echo "## 2. 道具"
for c in perf gcc python3 numactl readelf tmux; do printf '%-10s ' "$c"; command -v "$c" || echo "なし"; done
perf --version 2>&1 | head -1
echo "perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null) nmi_watchdog=$(cat /proc/sys/kernel/nmi_watchdog 2>/dev/null)"
echo "PY=${PY:-（なし）}  $("${PY:-python3}" -c 'import plasma_perf, sys; print("plasma_perf ok", sys.executable)' 2>&1 | tail -1)"

echo; echo "## 3. ライブラリ"
echo "PLASMA_TEST=${PLASMA_TEST}"
ldd "${PLASMA_TEST}" | grep -E 'plasma|openblas|mkl|gomp|iomp|gfortran'
LIBOB=$(ldd "${PLASMA_TEST}" | awk '/libopenblas/ {print $3; exit}')
LIBIOMP=$(ldd "${PLASMA_TEST}" | awk '/libiomp5/ {print $3; exit}')
LIBGOMP=$(ldd "${PLASMA_TEST}" | awk '/libgomp\.so/ {print $3; exit}')
for f in "${PLASMA_TEST}" $(ldd "${PLASMA_TEST}" | awk '/libplasma/ {print $3}') "${LIBOB}" "${LIBIOMP}" "${LIBGOMP}"; do
    [ -n "${f}" ] || continue
    r=$(readlink -f "${f}")
    echo "### ${f} -> ${r}"
    echo "md5 $(md5sum "${r}" | cut -d' ' -f1)  mtime $(stat -c %y "${r}" | cut -d. -f1)"
done
echo "### openblas_get_config"
python3 -c "import ctypes,sys; l=ctypes.CDLL(sys.argv[1]); l.openblas_get_config.restype=ctypes.c_char_p; print(l.openblas_get_config().decode())" "${LIBOB}"
echo "### libgomp の版"
dpkg-query -W -f='${Package} ${Version}\n' 'libgomp1*' 2>/dev/null
echo "### libiomp5 の版"
strings -a "$(readlink -f "${LIBIOMP}")" | grep -m3 -iE 'Intel\(R\) OMP|LLVM OMP|OpenMP.*version|Compiler.*20[0-9][0-9]'

echo; echo "## 4. gompfwd.so"
bash "${HERE}/mk_gompfwd.sh" "${PLASMA_TEST}" "${HERE}/gompfwd"
cp "${HERE}"/gompfwd/{gompfwd_syms.txt,gompfwd_missing.txt} "${OUT}/" 2>/dev/null

echo; echo "## 5. 結び付き（LD_DEBUG=bindings、p = 4、size 512）"
for rt in iomp gompnaive gomp; do
    rt_set "${rt}" || continue
    rm -f "${OUT}/bind_${rt}".*
    OMP_NUM_THREADS=4 LD_DEBUG=bindings LD_DEBUG_OUTPUT="${OUT}/bind_${rt}" \
        numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" "${PLASMA_TEST}" dgeqrf --dim=512x512 --nb=64 --ib=16 --test=n > /dev/null 2>&1
    cat "${OUT}/bind_${rt}".* 2>/dev/null | sed -E 's/^[[:space:]]*[0-9]+:[[:space:]]*//' \
        | grep -E "symbol \`(GOMP_|omp_|__kmpc_|kmp_|__kmp)" | sort -u | gzip -c > "${OUT}/bind_${rt}.txt.gz"
    rm -f "${OUT}/bind_${rt}".[0-9]*
    echo "### ${rt}（LD_PRELOAD=$(rt_preload "")）：参照するファイル → 結び付いた先（OpenMP の名前の数）"
    zcat "${OUT}/bind_${rt}.txt.gz" | sed -E 's/^binding file ([^ ]+) \[[0-9]+\] to ([^ ]+) \[[0-9]+\].*/\1 -> \2/' \
        | awk '{n=split($1,a,"/"); m=split($3,b,"/"); print a[n], "->", b[m]}' | sort | uniq -c
    echo "  plasma・openblas・plasmatest から libiomp5 に結び付いた名前の数（gomp では 0 のはず）: $(zcat "${OUT}/bind_${rt}.txt.gz" | grep -cE '^binding file [^ ]*(plasma|openblas)[^ ]* .* to [^ ]*libiomp5')"
    echo "  plasma・openblas・plasmatest から libgomp に結び付いた名前の数（iomp では 0 のはず）: $(zcat "${OUT}/bind_${rt}.txt.gz" | grep -cE '^binding file [^ ]*(plasma|openblas)[^ ]* .* to [^ ]*libgomp')"
done

echo; echo "## 6. 実行中のプロセス（p = 32、size 4096・nb 32）"
for rt in ${RT_ALL}; do
    rt_proc_check "${rt}" 32 "${OUT}/proc_${rt}.txt"
    echo "### ${rt}"
    grep -E '^# |^threads=|^distinct_cpus=' "${OUT}/proc_${rt}.txt"
    sed -n '/^## environ/,/^threads=/p' "${OUT}/proc_${rt}.txt" | grep -v -E '^##|^threads=' | sed 's/^/  environ: /'
    sed -n '/^## maps/,/^## 各スレッド/p' "${OUT}/proc_${rt}.txt" | grep -v '^##' | sed 's/^/  maps: /'
done
echo "（threads が 32 なら、スレッドを作ったランタイムは1つ。distinct_cpus が 32 なら1スレッド1コア）"

echo; echo "## 7. 検算（p = 32、size 1024、nb 64、ib 16、--test=y）"
for rt in ${RT_ALL}; do
    rt_set "${rt}" || continue
    echo "### ${rt}"
    OMP_NUM_THREADS=32 numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" \
        "${PLASMA_TEST}" dgeqrf --dim=1024x1024 --nb=64 --ib=16 --test=y 2>&1 | tail -3
done

echo; echo "## 8. obshim（stamp、p = 32、size 2048、nb 96、ib 48）"
gcc -O2 -Wall -fPIC -shared -o "${SO}" "${HERE}/shim/obshim.c" -ldl -lpthread && echo "build ok: ${SO}"
for rt in ${RT_ALL}; do
    rt_set "${rt}" || continue
    o=$(mktemp)
    OMP_NUM_THREADS=32 numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "${SO}")" OBSHIM_MODE=stamp OBSHIM_OUT="${o}" \
        "${PLASMA_TEST}" dgeqrf --dim=2048x2048 --nb=96 --ib=48 --test=n | awk -v rt="${rt}" '$3 ~ /^[0-9]+\.[0-9]+$/ && $4 ~ /^[0-9]+\.[0-9]+$/ {printf "%s Time=%s Gflop/s=%s  ", rt, $3, $4; exit}'
    python3 -c "
import json, sys
m = [json.loads(l) for l in open(sys.argv[1]) if '\"meta\"' in l]
print('stamps=%s threads_seen=%s' % (m[0].get('stamps'), m[0].get('threads_seen')) if m else 'meta が無い')" "${o}"
    rm -f "${o}"
done
echo "（stamps が 2 以上なら、計時の区間が取れている）"

echo; echo "## 11. perf のイベント"
for f in ${ZEN3_FILLS}; do
    printf '%-40s ' "ls_dmnd_fills_from_sys.${f}"
    if perf list 2>/dev/null | grep -q "ls_dmnd_fills_from_sys.${f}\b"; then echo "あり  符号: $(perfev_enc "ls_dmnd_fills_from_sys.${f}")"; else echo "なし"; fi
done
echo "### perf list --details（ls_dmnd_fills_from_sys の節）"
perf list --details 'ls_dmnd_fills_from_sys*' 2>&1 | head -40
if perfev_resolve; then
    echo "PERF_B_KIND=${PERF_B_KIND}"
    echo "perfA: ${PERF_A_REC[*]}"
    echo "perfB: ${PERF_B_REC[*]}"
    echo "### perf record の試し（gdef、size 4096、nb 64、ib 32。どのイベントにも試料があるか、失った試料が無いか）"
    rt_set gdef
    tmp=$(mktemp -d)
    for g in A B; do
        if [ "${g}" = A ]; then ev=("${PERF_A_REC[@]}"); else ev=("${PERF_B_REC[@]}"); fi
        OMP_NUM_THREADS=32 perf record -k CLOCK_MONOTONIC "${ev[@]}" -o "${tmp}/${g}.data" -- \
            numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" "${PLASMA_TEST}" dgeqrf --dim=4096x4096 --nb=64 --ib=32 --test=n \
            > /dev/null 2> "${tmp}/${g}.rec.txt"
        echo "組 ${g}: perf record exit=$?  $(grep -v WARNING "${tmp}/${g}.rec.txt" | tail -2 | tr '\n' ' ')"
        grep -iE 'lost|error|invalid|unknown' "${tmp}/${g}.rec.txt" | head -5
        s0=${SECONDS}
        perf script -i "${tmp}/${g}.data" --show-task-events -F comm,tid,time,period,event,ip,sym,symoff,dso 2> "${tmp}/${g}.err" > "${tmp}/${g}.txt"
        echo "  perf script $(( SECONDS - s0 )) 秒、$(wc -l < "${tmp}/${g}.txt") 行、データ $(du -h "${tmp}/${g}.data" | cut -f1)"
        echo "  最初の試料の行: $(grep -m1 -vE 'PERF_RECORD' "${tmp}/${g}.txt")"
        echo "  イベントごとの試料の数:"
        grep -vE 'PERF_RECORD' "${tmp}/${g}.txt" | awk '{for (i = 1; i <= NF; i++) if ($i ~ /:$/ && $(i-1) ~ /^[0-9]+$/ && $(i-2) ~ /^[0-9]+\.[0-9]+:$/) {print $i; break}}' \
            | sort | uniq -c | sed 's/^/    /'
        printf '{"type":"stamp","mono_ns":0}\n{"type":"stamp","mono_ns":99999999999999999}\n' > "${tmp}/s.jsonl"
        python3 "${HERE}/reduce_perfev.py" --shim "${tmp}/s.jsonl" --threads 32 --out "${tmp}/${g}" < "${tmp}/${g}.txt" | sed 's/^/  /'
    done
    rm -rf "${tmp}"
else
    echo "[ERROR] perf のイベントの組が作れなかった（上の表）。TASK.md §3.1 の 11 に従う"
fi

echo; echo "## 12. 時分割（perf stat、size 4096、nb 64、ib 32。idef・gdef。各行の 5 列目が数えた割合）"
if [ -n "${PERF_A_STAT:-}" ]; then
    for rt in idef gdef; do
        rt_set "${rt}"
        for g in A B; do
            if [ "${g}" = A ]; then ev=${PERF_A_STAT}; else ev=${PERF_B_STAT}; fi
            echo "### ${rt} 組 ${g}"
            OMP_NUM_THREADS=32 perf stat -x, -e "${ev}" -- \
                numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" "${PLASMA_TEST}" dgeqrf --dim=4096x4096 --nb=64 --ib=32 --test=n \
                2>&1 >/dev/null | grep -v '^#' | grep -v '^$' | sed 's/^/  /'
        done
    done
fi

echo; echo "## 13. 待ち方の変数（size 1024、nb 64、ib 16）"
for rt in ${RT_ALL}; do
    rt_set "${rt}"
    echo "### ${rt}: $(rt_env_line)"
    case "${rt}" in
        idef|ibt0)
            OMP_NUM_THREADS=32 KMP_SETTINGS=1 numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" \
                "${PLASMA_TEST}" dgeqrf --dim=1024x1024 --nb=64 --ib=16 --test=n 2>&1 | grep -E 'KMP_BLOCKTIME|OMP_WAIT_POLICY|KMP_LIBRARY' | sed 's/^/  /' ;;
        gdef|gact)
            OMP_NUM_THREADS=32 OMP_DISPLAY_ENV=true numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" \
                "${PLASMA_TEST}" dgeqrf --dim=1024x1024 --nb=64 --ib=16 --test=n 2>&1 | grep -E 'OMP_WAIT_POLICY|GOMP_SPINCOUNT|OMP_PROC_BIND|OMP_PLACES|_OPENMP' | sed 's/^/  /' ;;
    esac
done

echo; echo "## 14. size 1024 のスイープの試し（gdef、plasma_perf、nb 96、ib 8〜48 の先頭 2 点だけ）"
tmp=$(mktemp -d)
rt_set gdef
"${PY:-python3}" -m plasma_perf bench tileqr --size 1024 --threads 32 --nb-min 96 --nb-max 96 --ib-min 8 --ib-max 12 --step 4 --trials 1 \
    --wrapper "env LD_PRELOAD=${GOMPFWD}" --output "${tmp}/t.csv" 2>&1 | tail -4 | sed 's/^/  /'
echo "  結果: $(tr '\n' ' ' < "${tmp}/t.csv" 2>/dev/null)"
# plasma_perf と同じ渡し方で、子の plasmatest に固定と LD_PRELOAD が届くか（environ と maps）
OMP_NUM_THREADS=32 "${PY:-python3}" - "${PLASMA_TEST}" "${GOMPFWD}" > "${tmp}/env.txt" 2>&1 <<'EOF'
import os, subprocess, sys, time
p = subprocess.Popen(["env", "LD_PRELOAD=" + sys.argv[2], sys.argv[1], "dgeqrf", "--dim=4096x4096", "--nb=32", "--ib=16", "--test=n"],
                     stdout=subprocess.DEVNULL, env=os.environ.copy())
time.sleep(1.0)
env = open(f"/proc/{p.pid}/environ").read().split("\0")
print("\n".join(e for e in env if e.startswith(("OMP_", "KMP_", "GOMP_", "LD_PRELOAD"))))
maps = open(f"/proc/{p.pid}/maps").read()
print("maps:", sorted({l.split()[-1].split("/")[-1] for l in maps.splitlines() if any(k in l for k in ("libgomp", "libiomp5", "gompfwd"))}))
cpus = sorted({open(f"/proc/{p.pid}/task/{t}/status").read().split("Cpus_allowed_list:")[1].split()[0] for t in os.listdir(f"/proc/{p.pid}/task")})
print("threads=%d distinct_cpus=%d" % (len(os.listdir(f"/proc/{p.pid}/task")), len(cpus)))
p.kill(); p.wait()
EOF
sed 's/^/  /' "${tmp}/env.txt"
rm -rf "${tmp}"
echo "（OMP_PLACES=cores・OMP_PROC_BIND=close・LD_PRELOAD=…gompfwd.so が届き、maps に gompfwd と libgomp、distinct_cpus=32 なら固定が効いている）"

echo; echo "# preflight 終わり。${OUT} を持ち帰る"
