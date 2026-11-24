#!/usr/bin/env bash
# =============================================================================
# 1_run.sh — EPYC 7543 で OpenMP のランタイムだけを libiomp5 → libgomp に替えて比べる（2026-11-24）
#
#   同じ plasmatest・/usr/local/lib の libplasma・OpenBLAS 0.3.30.dev のまま、
#     iomp  いままでと同じ（libiomp5）
#     gomp  gompfwd.so を LD_PRELOAD して GOMP_*・omp_* を libgomp に結び付ける（rt.sh・mk_gompfwd.sh）
#   を、1つの実行の中で点ごとに交互に流す（round ごとに順番を入れ替える）。
#   全実行に obshim.so を LD_PRELOAD し（gomp では obshim の後ろに gompfwd）、計時区間の時刻で perf を切る。
#
#   種類（KINDS）  obshim     何が取れるか
#     stamp        stamp      素の時間
#     pass         pass       alloc／free の回数・1回あたりの時間（OpenBLAS の共有の表の取り合いの大きさ）
#     perf         stamp      perf の内訳。libgomp は試料をファイル上のアドレスでも残す（_gompoff.csv）
#     tls・perftls tls        表の差し替え（スレッドごとに使い回す）。既定では流さない（必要になったら KINDS に足す）
#   別に PERFG の条件だけ、round 1 で perf --call-graph dwarf を取る（どこから libgomp のロック待ちに入ったか）。
#   ib は過去のベンチマークの最適な ib（best_ib_epyc7543_p32.txt）。plasmatest は --test=n。1点ごとに新しいプロセス。
#
#   使い方（plasma-bench の直下で。ブランチ feat/libgomp-ab。S=scripts/hpc/epyc7543/campaigns/2026-11-24_libgomp-ab。先に 0_preflight.sh を流し、結果を確かめてから）:
#     MACHINE=epyc7543 bash $S/1_run.sh            （tmux の中で。見込み 30〜60 分。gomp の nb 32 が長ければ延びる）
#   変数:
#     RTS（既定 "iomp gomp"） KINDS（既定 "stamp pass perf"） THREADS（既定 32） SIZES（既定 "1024 2048 4096"）
#     NB_LIST（既定 "32 64 96 128 192"） ROUNDS（既定 3） IB_TABLE（既定 best_ib_epyc7543_p32.txt）
#     PERFG（既定 "4096:32 4096:64 4096:128"、空で流さない） PERFG_RTS（既定 "gomp"） PERFG_FREQ（既定 299）
#     TIMEOUT（既定 900 秒） MAX_OTHER_CPU（既定 100） FORCE
#   結果: results/benchmark/<config>_libgomp-ab_<時刻>/（これをディレクトリごと devbox-lab の ~/Download/epyc/ に持ち帰る）
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

RTS=${RTS:-"iomp gomp"}
KINDS=${KINDS:-"stamp pass perf"}
THREADS=${THREADS:-32}
SIZES=${SIZES:-"1024 2048 4096"}
NB_LIST=${NB_LIST:-"32 64 96 128 192"}
ROUNDS=${ROUNDS:-3}
IB_TABLE=${IB_TABLE:-${HERE}/best_ib_epyc7543_p32.txt}
PERFG=${PERFG-"4096:32 4096:64 4096:128"}
PERFG_RTS=${PERFG_RTS:-gomp}
PERFG_FREQ=${PERFG_FREQ:-299}
TIMEOUT=${TIMEOUT:-900}
MAX_OTHER_CPU=${MAX_OTHER_CPU:-100}
FORCE=${FORCE:-0}
read -r -a RT_LIST <<< "${RTS}"
read -r -a KIND_LIST <<< "${KINDS}"
read -r -a THREAD_LIST <<< "${THREADS}"
[ -f "${IB_TABLE}" ] || { echo "[ERROR] IB_TABLE が無い: ${IB_TABLE}"; exit 1; }

hpc_setup
SO="${HERE}/shim/obshim.so"
if [ ! -f "${SO}" ] || [ "${HERE}/shim/obshim.c" -nt "${SO}" ]; then
    gcc -O2 -Wall -fPIC -shared -o "${SO}" "${HERE}/shim/obshim.c" -ldl -lpthread
fi
GOMPFWD="${HERE}/gompfwd/gompfwd.so"
if [[ " ${RTS} ${PERFG_RTS} " == *" gomp "* ]] && [ ! -f "${GOMPFWD}" ]; then
    bash "${HERE}/mk_gompfwd.sh" "${PLASMA_TEST}" "${HERE}/gompfwd"
fi
if [ -s "${HERE}/gompfwd/gompfwd_missing.txt" ] && [ "${FORCE}" != 1 ]; then
    echo "[ERROR] libgomp に無い OpenMP の名前がある（${HERE}/gompfwd/gompfwd_missing.txt）。gomp の条件が libiomp5 と混ざる。FORCE=1 で無視"
    exit 1
fi
other_cpu() { ps -eo user:20,pcpu | awk -v me="$(id -un)" 'NR>1 && $1!=me {s+=$2} END{printf "%.0f", s+0}'; }
oc=$(other_cpu)
if [ "${oc}" -gt "${MAX_OTHER_CPU}" ] && [ "${FORCE}" != 1 ]; then
    echo "[ERROR] 他のユーザーの CPU 使用率の合計が ${oc}%（上限 ${MAX_OTHER_CPU}%）。共用機なので中止。FORCE=1 で無視できる"
    ps -eo user:20,pcpu,etime,args --sort=-pcpu | head -6
    exit 1
fi
for k in "${KIND_LIST[@]}"; do
    case "${k}" in stamp|pass|tls|perf|perftls) ;; *) echo "[ERROR] unknown kind: ${k}"; exit 1 ;; esac
done
PERF_EV=cpu-clock
[ "$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo 2)" -ge 2 ] && PERF_EV=cpu-clock:u

ib_of() {   # $1=size $2=nb
    local v; v=$(awk -v s="$1" -v n="$2" '!/^#/ && $1 == s && $2 == n {print $3; exit}' "${IB_TABLE}")
    [ -n "${v}" ] || { echo "[ERROR] ${IB_TABLE} に size ${1} nb ${2} が無い" >&2; exit 1; }
    echo "${v}"
}
CONDS=()
for p in "${THREAD_LIST[@]}"; do for s in ${SIZES}; do for nb in ${NB_LIST}; do CONDS+=("${s} ${nb} ${p} $(ib_of "${s}" "${nb}")"); done; done; done

OUTDIR="results/benchmark/${CONFIG}_libgomp-ab_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${OUTDIR}/raw" "${OUTDIR}/shim" "${OUTDIR}/perf"
CSV="${OUTDIR}/runs.csv"
echo "round,rt,kind,mode,threads,size,nb,ib,Time_sec,GFlops,freq,other_cpu_before,load1_before,exit,elapsed_sec" > "${CSV}"

MAX_P=$(printf '%s\n' "${THREAD_LIST[@]}" | sort -n | tail -n 1)
hpc_select_nodes "${MAX_P}"
hpc_meta > "${OUTDIR}/meta.txt"
LIB=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libopenblas/ {print $3}')")
LIBGOMP=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libgomp\.so/ {print $3; exit}')")
LIBIOMP=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libiomp5/ {print $3; exit}')")
{
    echo
    echo "# === 計測条件 ==="
    echo "MACHINE=${MACHINE} RTS=${RTS} KINDS=${KINDS} THREADS=${THREADS} SIZES=${SIZES} NB_LIST=${NB_LIST} ROUNDS=${ROUNDS} IB_TABLE=${IB_TABLE} PERFG=${PERFG} PERFG_RTS=${PERFG_RTS} PERFG_FREQ=${PERFG_FREQ}"
    echo "# IB_TABLE の中身"; grep -v '^#' "${IB_TABLE}"
    echo "perf: $(perf --version 2>&1 | head -1)   PERF_EV=${PERF_EV}   perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null)"
    echo "libopenblas: ${LIB}  md5 $(md5sum "${LIB}" | cut -d' ' -f1)"
    python3 -c "import ctypes,sys; l=ctypes.CDLL(sys.argv[1]); l.openblas_get_config.restype=ctypes.c_char_p; print('openblas_get_config:', l.openblas_get_config().decode())" "${LIB}"
    echo "libiomp5: ${LIBIOMP}  md5 $(md5sum "${LIBIOMP}" | cut -d' ' -f1)"
    echo "libgomp: ${LIBGOMP}  md5 $(md5sum "${LIBGOMP}" | cut -d' ' -f1)  $(dpkg-query -W -f='${Package} ${Version}' libgomp1 2>/dev/null)"
    [ -f "${GOMPFWD}" ] && echo "gompfwd: md5 $(md5sum "${GOMPFWD}" | cut -d' ' -f1)  中継 $(wc -l < "${HERE}/gompfwd/gompfwd_syms.txt") 個: $(tr '\n' ' ' < "${HERE}/gompfwd/gompfwd_syms.txt")"
    echo "obshim md5: $(md5sum "${SO}" | cut -d' ' -f1)"
    echo "待ち方: OMP_WAIT_POLICY=${OMP_WAIT_POLICY:-（既定）} KMP_BLOCKTIME=${KMP_BLOCKTIME:-（既定）} GOMP_SPINCOUNT=${GOMP_SPINCOUNT:-（既定）}"
} >> "${OUTDIR}/meta.txt"
readelf -lW "${LIBGOMP}" > "${OUTDIR}/libgomp_loads.txt"
for P in "${THREAD_LIST[@]}"; do
    hpc_select_nodes "${P}"
    hpc_set_cond close bind
    for rt in "${RT_LIST[@]}"; do
        rt_proc_check "${rt}" "${P}" "${OUTDIR}/affinity_${rt}_p${P}.txt" "${SO}"
        echo "affinity ${rt} p${P}: $(grep -E '^threads=|^distinct_cpus=' "${OUTDIR}/affinity_${rt}_p${P}.txt" | tr '\n' ' ') maps: $(sed -n '/^## maps/,/^## 各/p' "${OUTDIR}/affinity_${rt}_p${P}.txt" | grep -v '^##' | awk '{n=split($2,a,"/"); print a[n]}' | sort -u | tr '\n' ' ')"
    done
done | tee -a "${OUTDIR}/meta.txt"
echo "output: ${OUTDIR}"
hpc_snapshot "開始" | tee -a "${OUTDIR}/meta.txt"

mode_of() { case "$1" in stamp|perf|perfg) echo stamp ;; pass) echo pass ;; tls|perftls) echo tls ;; esac; }

# 1 実行。$1=kind $2=round $3=size $4=nb $5=p $6=ib $7=rt
run_one() {
    local kind=$1 round=$2 size=$3 nb=$4 P=$5 ib=$6 rt=$7 mode
    mode=$(mode_of "${kind}")
    local tag="${kind}_${rt}_p${P}_s${size}_nb${nb}_r${round}"
    local raw="${OUTDIR}/raw/${tag}.txt" sj="${OUTDIR}/shim/${tag}.jsonl" freq="" ex=0 t0=${SECONDS} oc1 ld1
    oc1=$(other_cpu); ld1=$(cut -d' ' -f1 /proc/loadavg)
    hpc_select_nodes "${P}"
    hpc_set_cond close bind
    rt_set "${rt}"
    export OMP_NUM_THREADS="${P}"
    local -a cmd=(numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "${SO}")" OBSHIM_MODE="${mode}" OBSHIM_OUT="${sj}"
                  "${PLASMA_TEST}" dgeqrf --dim="${size}x${size}" --nb="${nb}" --ib="${ib}" --test=n)
    local pd="${OUTDIR}/perf/${tag}.data"
    case "${kind}" in
        stamp|pass|tls)
            timeout "${TIMEOUT}" "${cmd[@]}" > "${raw}" 2>&1 || ex=$? ;;
        perf|perftls)
            if   [ "${size}" -le 2048 ]; then freq=9999
            elif [ "${nb}" -le 64 ]; then freq=999; else freq=1999; fi
            timeout "${TIMEOUT}" perf record -k CLOCK_MONOTONIC -e "${PERF_EV}" -F "${freq}" -o "${pd}" -- "${cmd[@]}" \
                > "${raw}" 2> "${OUTDIR}/perf/${tag}.rec.txt" || ex=$? ;;
        perfg)
            freq=${PERFG_FREQ}
            timeout "${TIMEOUT}" perf record -k CLOCK_MONOTONIC -e "${PERF_EV}" -F "${freq}" --call-graph dwarf,4096 -o "${pd}" -- "${cmd[@]}" \
                > "${raw}" 2> "${OUTDIR}/perf/${tag}.rec.txt" || ex=$? ;;
    esac
    local t gf
    read -r t gf < <(awk '$3 ~ /^[0-9]+\.[0-9]+$/ && $4 ~ /^[0-9]+\.[0-9]+$/ {print $3, $4; exit}' "${raw}" || true)
    case "${kind}" in
        perf|perftls)
            perf script -i "${pd}" --show-task-events --show-mmap-events -F comm,tid,time,ip,sym,symoff,dso 2> "${OUTDIR}/perf/${tag}.script.err" \
                | tee >( [ "${round}" = 1 ] && gzip -c > "${OUTDIR}/perf/${tag}.script.gz" || cat > /dev/null ) \
                | python3 "${HERE}/reduce_perf.py" --shim "${sj}" --threads "${P}" --time "${t:-0}" --freq "${freq}" \
                      --out "${OUTDIR}/perf/${tag}" || echo "[warn] reduce 失敗: ${tag}" | tee -a "${OUTDIR}/run.log"
            rm -f "${pd}" ;;
        perfg)
            perf script -i "${pd}" --show-task-events --show-mmap-events -F comm,tid,time,ip,sym,symoff,dso 2> "${OUTDIR}/perf/${tag}.script.err" \
                | tee >(gzip -c > "${OUTDIR}/perf/${tag}.script.gz") \
                | python3 "${HERE}/reduce_perfg.py" --shim "${sj}" --time "${t:-0}" --out "${OUTDIR}/perf/${tag}" \
                || echo "[warn] reduce 失敗: ${tag}" | tee -a "${OUTDIR}/run.log"
            rm -f "${pd}" ;;
    esac
    [ "${ex}" -ne 0 ] && echo "[warn] 終了コード ${ex}: ${tag}" | tee -a "${OUTDIR}/run.log"
    echo "${round},${rt},${kind},${mode},${P},${size},${nb},${ib},${t:-},${gf:-},${freq},${oc1},${ld1},${ex},$(( SECONDS - t0 ))" >> "${CSV}"
    printf "r%d %-4s %-6s p%-3d size %5d nb %3d ib %3d  %8s s  %8s GFlops  other_cpu %s%%  (%d sec)\n" \
        "${round}" "${rt}" "${kind}" "${P}" "${size}" "${nb}" "${ib}" "${t:--}" "${gf:--}" "${oc1}" "${SECONDS}"
}

nk=${#KIND_LIST[@]}; nr=${#RT_LIST[@]}
echo "===== start（条件 ${#CONDS[@]} × ランタイム ${nr} × 種類 ${nk} × ${ROUNDS} 回 ＋ perfg）====="
SECONDS=0
for round in $(seq 1 "${ROUNDS}"); do
    ci=0
    for c in "${CONDS[@]}"; do
        read -r size nb P ib <<< "${c}"
        for j in $(seq 0 $(( nr - 1 ))); do
            rt=${RT_LIST[$(( (j + round + ci) % nr ))]}       # ランタイムの順番を round と条件ごとに入れ替える
            for i in $(seq 0 $(( nk - 1 ))); do
                run_one "${KIND_LIST[$(( (i + round) % nk ))]}" "${round}" "${size}" "${nb}" "${P}" "${ib}" "${rt}"
            done
        done
        ci=$(( ci + 1 ))
    done
    echo "--- round ${round} 終わり: $(uptime) / other_cpu=$(other_cpu)% ---" | tee -a "${OUTDIR}/run.log"
done
if [ -n "${PERFG}" ] && command -v perf > /dev/null; then
    for e in ${PERFG}; do
        IFS=: read -r s nb <<< "${e}"
        for rt in ${PERFG_RTS}; do
            for P in "${THREAD_LIST[@]}"; do run_one perfg 1 "${s}" "${nb}" "${P}" "$(ib_of "${s}" "${nb}")" "${rt}"; done
        done
    done
fi
echo "===== end (${SECONDS} sec) ====="
hpc_snapshot "終了" | tee -a "${OUTDIR}/meta.txt"
echo "結果: ${OUTDIR}（ディレクトリごと devbox-lab の ~/Download/epyc/ に持ち帰る。perf.data は消してある）"
