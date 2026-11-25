#!/usr/bin/env bash
# =============================================================================
# 1_run.sh — EPYC 7543 で OpenMP の待ち方をランタイムと交差させて比べる（2026-11-25、omp-wait-ab）
#
#   同じ plasmatest・/usr/local/lib の libplasma・OpenBLAS 0.3.30.dev のまま、p = 32 で 4 条件（rt.sh）：
#     idef（libiomp5、既定）  ibt0（libiomp5、KMP_BLOCKTIME=0）  gdef（libgomp、既定）  gact（libgomp、OMP_WAIT_POLICY=active）
#   を、1つの実行の中で点ごとに交互に流す（round ごとに順番を入れ替える）。
#   全実行に obshim.so（stamp）を LD_PRELOAD し（libgomp では obshim の後ろに gompfwd）、計時区間の時刻で perf を切る。
#
#   種類     round        何が取れるか
#     stamp  1〜5         素の時間（plasmatest の Time・GFlops）
#     perfA  1〜3         task-clock（100 µs）・cycles・instructions を関数ごとに → 計算の CPU 時間・周波数・IPC
#     perfB  1〜3         cycles と取り寄せ先 4 つ（Zen3）を関数ごとに → ほかの CCX・DRAM から取り寄せた割合
#   点：size 4096 の nb 64・96・128・192、size 2048 の nb 96・128。ib は best_ib_epyc7543_p32.txt（前回と同じ）。
#   合計 6 点 × 4 条件 × (5 ＋ 3 ＋ 3) ＝ 264 回。plasmatest は --test=n。1点ごとに新しいプロセス。
#
#   使い方（plasma-bench の直下で。ブランチ feat/omp-wait-ab。S=scripts/hpc/epyc7543/campaigns/2026-11-25_omp-wait-ab。
#           先に 0_preflight.sh を流し、結果を確かめてから）:
#     MACHINE=epyc7543 bash $S/1_run.sh            （tmux の中で。見込み 20〜30 分。perf の後処理が大半）
#   変数（計測の条件は変えない。動作の確認のときだけ使う）:
#     RTS（既定 "idef ibt0 gdef gact"） POINTS（既定 "4096:64 4096:96 4096:128 4096:192 2048:96 2048:128"）
#     STAMP_ROUNDS（既定 5） PERF_ROUNDS（既定 3） THREADS（既定 32） PERF_B（既定 zen3｜generic。perfev.sh）
#     TIMEOUT（既定 900 秒） MAX_OTHER_CPU（既定 100） FORCE
#   結果: results/benchmark/<config>_omp-wait-ab_<時刻>/（これをディレクトリごと devbox-lab の ~/Download/epyc/ に持ち帰る）
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACHINE=${MACHINE:?MACHINE=epyc7543 を付ける}
export MACHINE
PB="${PLASMA_BENCH:-$PWD}"
# shellcheck source=/dev/null
source "${PB}/scripts/hpc/lib/common.sh"
# shellcheck source=/dev/null
source "${HERE}/rt.sh"
# shellcheck source=/dev/null
source "${HERE}/perfev.sh"

RTS=${RTS:-${RT_ALL}}
POINTS=${POINTS:-"4096:64 4096:96 4096:128 4096:192 2048:96 2048:128"}
STAMP_ROUNDS=${STAMP_ROUNDS:-5}
PERF_ROUNDS=${PERF_ROUNDS:-3}
P=${THREADS:-32}
IB_TABLE=${IB_TABLE:-${HERE}/best_ib_epyc7543_p32.txt}
TIMEOUT=${TIMEOUT:-900}
MAX_OTHER_CPU=${MAX_OTHER_CPU:-100}
FORCE=${FORCE:-0}
read -r -a RT_LIST <<< "${RTS}"
[ -f "${IB_TABLE}" ] || { echo "[ERROR] IB_TABLE が無い: ${IB_TABLE}"; exit 1; }
command -v perf > /dev/null || { echo "[ERROR] perf が無い"; exit 1; }
perfev_resolve || exit 1

hpc_setup
SO="${HERE}/shim/obshim.so"
if [ ! -f "${SO}" ] || [ "${HERE}/shim/obshim.c" -nt "${SO}" ]; then
    gcc -O2 -Wall -fPIC -shared -o "${SO}" "${HERE}/shim/obshim.c" -ldl -lpthread
fi
GOMPFWD="${HERE}/gompfwd/gompfwd.so"
[ -f "${GOMPFWD}" ] || bash "${HERE}/mk_gompfwd.sh" "${PLASMA_TEST}" "${HERE}/gompfwd"
if [ -s "${HERE}/gompfwd/gompfwd_missing.txt" ] && [ "${FORCE}" != 1 ]; then
    echo "[ERROR] libgomp に無い OpenMP の名前がある（${HERE}/gompfwd/gompfwd_missing.txt）。libgomp の条件が libiomp5 と混ざる。FORCE=1 で無視"
    exit 1
fi
other_cpu() { ps -eo user:20,pcpu | awk -v me="$(id -un)" 'NR>1 && $1!=me {s+=$2} END{printf "%.0f", s+0}'; }
oc=$(other_cpu)
if [ "${oc}" -gt "${MAX_OTHER_CPU}" ] && [ "${FORCE}" != 1 ]; then
    echo "[ERROR] 他のユーザーの CPU 使用率の合計が ${oc}%（上限 ${MAX_OTHER_CPU}%）。共用機なので中止。FORCE=1 で無視できる"
    ps -eo user:20,pcpu,etime,args --sort=-pcpu | head -6
    exit 1
fi

ib_of() {   # $1=size $2=nb
    local v; v=$(awk -v s="$1" -v n="$2" '!/^#/ && $1 == s && $2 == n {print $3; exit}' "${IB_TABLE}")
    [ -n "${v}" ] || { echo "[ERROR] ${IB_TABLE} に size ${1} nb ${2} が無い" >&2; exit 1; }
    echo "${v}"
}
CONDS=()
for e in ${POINTS}; do IFS=: read -r s nb <<< "${e}"; CONDS+=("${s} ${nb} $(ib_of "${s}" "${nb}")"); done

OUTDIR="results/benchmark/${CONFIG}_omp-wait-ab_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${OUTDIR}/raw" "${OUTDIR}/shim" "${OUTDIR}/perf"
CSV="${OUTDIR}/runs.csv"
echo "round,rt,kind,threads,size,nb,ib,Time_sec,GFlops,other_cpu_before,load1_before,exit,elapsed_sec" > "${CSV}"

hpc_select_nodes "${P}"
hpc_meta > "${OUTDIR}/meta.txt"
LIB=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libopenblas/ {print $3}')")
LIBGOMP=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libgomp\.so/ {print $3; exit}')")
LIBIOMP=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libiomp5/ {print $3; exit}')")
{
    echo
    echo "# === 計測条件 ==="
    echo "MACHINE=${MACHINE} RTS=${RTS} POINTS=${POINTS} STAMP_ROUNDS=${STAMP_ROUNDS} PERF_ROUNDS=${PERF_ROUNDS} THREADS=${P} IB_TABLE=${IB_TABLE}"
    echo "# IB_TABLE の中身"; grep -v '^#' "${IB_TABLE}"
    echo "perf: $(perf --version 2>&1 | head -1)   perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null)   nmi_watchdog=$(cat /proc/sys/kernel/nmi_watchdog 2>/dev/null)"
    echo "PERF_B_KIND=${PERF_B_KIND}"
    echo "perfA: ${PERF_A_REC[*]}"
    echo "perfB: ${PERF_B_REC[*]}"
    echo "libopenblas: ${LIB}  md5 $(md5sum "${LIB}" | cut -d' ' -f1)"
    python3 -c "import ctypes,sys; l=ctypes.CDLL(sys.argv[1]); l.openblas_get_config.restype=ctypes.c_char_p; print('openblas_get_config:', l.openblas_get_config().decode())" "${LIB}"
    echo "libiomp5: ${LIBIOMP}  md5 $(md5sum "${LIBIOMP}" | cut -d' ' -f1)"
    echo "libgomp: ${LIBGOMP}  md5 $(md5sum "${LIBGOMP}" | cut -d' ' -f1)  $(dpkg-query -W -f='${Package} ${Version}' libgomp1 2>/dev/null)"
    echo "gompfwd: md5 $(md5sum "${GOMPFWD}" | cut -d' ' -f1)  中継 $(wc -l < "${HERE}/gompfwd/gompfwd_syms.txt") 個"
    echo "obshim md5: $(md5sum "${SO}" | cut -d' ' -f1)"
    echo "# 条件ごとの環境変数"
    for rt in "${RT_LIST[@]}"; do rt_set "${rt}"; echo "${rt}: $(rt_env_line)"; done
} >> "${OUTDIR}/meta.txt"
hpc_set_cond close bind
for rt in "${RT_LIST[@]}"; do
    rt_proc_check "${rt}" "${P}" "${OUTDIR}/affinity_${rt}_p${P}.txt" "${SO}"
    echo "affinity ${rt} p${P}: $(grep -E '^threads=|^distinct_cpus=' "${OUTDIR}/affinity_${rt}_p${P}.txt" | tr '\n' ' ') maps: $(sed -n '/^## maps/,/^## 各/p' "${OUTDIR}/affinity_${rt}_p${P}.txt" | grep -v '^##' | awk '{n=split($2,a,"/"); print a[n]}' | sort -u | tr '\n' ' ')"
done | tee -a "${OUTDIR}/meta.txt"
echo "output: ${OUTDIR}"
hpc_snapshot "開始" | tee -a "${OUTDIR}/meta.txt"

# 1 実行。$1=kind $2=round $3=size $4=nb $5=ib $6=rt
run_one() {
    local kind=$1 round=$2 size=$3 nb=$4 ib=$5 rt=$6
    local tag="${kind}_${rt}_p${P}_s${size}_nb${nb}_r${round}"
    local raw="${OUTDIR}/raw/${tag}.txt" sj="${OUTDIR}/shim/${tag}.jsonl" ex=0 t0=${SECONDS} oc1 ld1
    oc1=$(other_cpu); ld1=$(cut -d' ' -f1 /proc/loadavg)
    hpc_set_cond close bind
    rt_set "${rt}"
    export OMP_NUM_THREADS="${P}"
    local -a cmd=(numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "${SO}")" OBSHIM_MODE=stamp OBSHIM_OUT="${sj}"
                  "${PLASMA_TEST}" dgeqrf --dim="${size}x${size}" --nb="${nb}" --ib="${ib}" --test=n)
    local pd="${OUTDIR}/perf/${tag}.data"
    case "${kind}" in
        stamp)
            timeout "${TIMEOUT}" "${cmd[@]}" > "${raw}" 2>&1 || ex=$? ;;
        perfA)
            timeout "${TIMEOUT}" perf record -k CLOCK_MONOTONIC "${PERF_A_REC[@]}" -o "${pd}" -- "${cmd[@]}" \
                > "${raw}" 2> "${OUTDIR}/perf/${tag}.rec.txt" || ex=$? ;;
        perfB)
            timeout "${TIMEOUT}" perf record -k CLOCK_MONOTONIC "${PERF_B_REC[@]}" -o "${pd}" -- "${cmd[@]}" \
                > "${raw}" 2> "${OUTDIR}/perf/${tag}.rec.txt" || ex=$? ;;
    esac
    local t gf
    read -r t gf < <(awk '$3 ~ /^[0-9]+\.[0-9]+$/ && $4 ~ /^[0-9]+\.[0-9]+$/ {print $3, $4; exit}' "${raw}" || true)
    if [ "${kind}" != stamp ]; then
        perf script -i "${pd}" --show-task-events -F comm,tid,time,period,event,ip,sym,symoff,dso 2> "${OUTDIR}/perf/${tag}.script.err" \
            | python3 "${HERE}/reduce_perfev.py" --shim "${sj}" --threads "${P}" --time "${t:-0}" --out "${OUTDIR}/perf/${tag}" \
            || echo "[warn] reduce 失敗: ${tag}" | tee -a "${OUTDIR}/run.log"
        rm -f "${pd}"
    fi
    [ "${ex}" -ne 0 ] && echo "[warn] 終了コード ${ex}: ${tag}" | tee -a "${OUTDIR}/run.log"
    echo "${round},${rt},${kind},${P},${size},${nb},${ib},${t:-},${gf:-},${oc1},${ld1},${ex},$(( SECONDS - t0 ))" >> "${CSV}"
    printf "r%d %-4s %-5s p%-3d size %5d nb %3d ib %3d  %8s s  %8s GFlops  other_cpu %s%%  (%d sec)\n" \
        "${round}" "${rt}" "${kind}" "${P}" "${size}" "${nb}" "${ib}" "${t:--}" "${gf:--}" "${oc1}" "${SECONDS}"
}

nr=${#RT_LIST[@]}
NROUND=$(( STAMP_ROUNDS > PERF_ROUNDS ? STAMP_ROUNDS : PERF_ROUNDS ))
echo "===== start（点 ${#CONDS[@]} × 条件 ${nr} × (stamp ${STAMP_ROUNDS} ＋ perfA ${PERF_ROUNDS} ＋ perfB ${PERF_ROUNDS})）====="
SECONDS=0
for round in $(seq 1 "${NROUND}"); do
    KL=()
    [ "${round}" -le "${STAMP_ROUNDS}" ] && KL+=(stamp)
    [ "${round}" -le "${PERF_ROUNDS}" ] && KL+=(perfA perfB)
    nk=${#KL[@]}
    ci=0
    for c in "${CONDS[@]}"; do
        read -r size nb ib <<< "${c}"
        for j in $(seq 0 $(( nr - 1 ))); do
            rt=${RT_LIST[$(( (j + round + ci) % nr ))]}       # 条件の順番を round と点ごとに入れ替える
            for i in $(seq 0 $(( nk - 1 ))); do
                run_one "${KL[$(( (i + round) % nk ))]}" "${round}" "${size}" "${nb}" "${ib}" "${rt}"
            done
        done
        ci=$(( ci + 1 ))
    done
    echo "--- round ${round} 終わり: $(uptime) / other_cpu=$(other_cpu)% ---" | tee -a "${OUTDIR}/run.log"
done
echo "===== end (${SECONDS} sec) ====="
hpc_snapshot "終了" | tee -a "${OUTDIR}/meta.txt"
echo "結果: ${OUTDIR}（ディレクトリごと devbox-lab の ~/Download/epyc/ に持ち帰る。perf.data は消してある）"
