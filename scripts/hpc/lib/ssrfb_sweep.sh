#!/usr/bin/env bash
# =============================================================================
# ssrfb_sweep.sh — DTSMQR カーネル単体（NoFlush）の1コア計測を、nb・ib の全格子で流す
#
#   β と A を決めるための計測（2026-10-28 のゼミで「β は1コアで決める」となった）。
#   1本のプロセスを1コアに固定し、同じノードで REPS の回数だけ順に流す。1反復 = 1 CSV。
#   ノードを専有するので、ほかのコアは空いている。
#
#   使い方（ジョブスクリプトから。変数で条件を渡す）:
#       MACHINE=flow SIZE=4096 REPS=1:2:3:4:5 bash scripts/hpc/lib/ssrfb_sweep.sh
#
#   変数:
#       MACHINE flow | aoba | epyc7543（必須）
#       SIZE    NoFlush の MAT_SIZE（キャッシュ占有量。既定 4096）
#       REPS    反復の番号を ":" でつなぐ（既定 1:2:3:4:5。ファイル名にだけ使う）
#       CORE    固定する CPU の番号（既定 1。CPU 0 は OS の割り込みを受けやすいので避ける）
#       STEP    nb・ib の刻み（既定 4）。NB_MIN / NB_MAX（既定 32 / 512）。N_IT（既定 50）
#
#   結果: results/benchmark/<config>_core<CORE>_ssrfb_size<S>_nb<min>-<max>_th1_r<REP>.csv
#         同じ名前の _meta.txt（計測条件）と _clock.txt（固定したコアのクロック。60 秒ごと）
#         1コアのクロックは全コア同時より高いはずなので、A を使うときに _clock.txt を見る。
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

SIZE=${SIZE:-4096}
REPS=${REPS:-1:2:3:4:5}
CORE=${CORE:-1}
STEP=${STEP:-4}
NB_MIN=${NB_MIN:-32}
NB_MAX=${NB_MAX:-512}
N_IT=${N_IT:-50}

hpc_setup
hpc_select_nodes 1

[ -x "${NOFLUSH_PATH}" ] || { echo "[ERROR] not executable: ${NOFLUSH_PATH}"; exit 1; }
if ldd "${NOFLUSH_PATH}" | grep -q 'not found'; then
    echo "[ERROR] unresolved libs"; ldd "${NOFLUSH_PATH}"; exit 1
fi

# 固定するコアと、そのコアの NUMA ノード。メモリは同じノードに置く
NODE=$(lscpu -p=CPU,NODE | awk -F, -v c="${CORE}" '!/^#/ && $1 == c {print $2}')
[ -n "${NODE}" ] || { echo "[ERROR] CPU ${CORE} が見つかりません"; exit 1; }
case ",${NODES}," in
    *,"${NODE}",*) ;;
    *) echo "[ERROR] CPU ${CORE}（NUMA ${NODE}）が使えるノード（${NODES}）の外にあります"; exit 1 ;;
esac
NUMACTL_ARGS=(--physcpubind="${CORE}" --membind="${NODE}")
hpc_check_cores 1

# 固定したコアのクロックを裏で記録する（/proc/cpuinfo の読み出しは 60 秒に1回だけ）
SAMPLER_PID=""
stop_sampler() {
    if [ -n "${SAMPLER_PID}" ]; then
        kill "${SAMPLER_PID}" 2>/dev/null || true
        wait "${SAMPLER_PID}" 2>/dev/null || true
        SAMPLER_PID=""
    fi
}
trap stop_sampler EXIT

start_sampler() {
    local out=$1
    (
        while :; do
            awk -v c="${CORE}" -v t="$(date '+%F %T')" '
                /^processor/ {cpu = $3}
                /^cpu MHz/ && cpu == c {print t, $4}' /proc/cpuinfo
            sleep 60
        done
    ) > "${out}" &
    SAMPLER_PID=$!
}

# 1反復ぶんのスイープ
run_rep() {
    local rep=$1
    local base="results/benchmark/${CONFIG}_core${CORE}_ssrfb_size${SIZE}_nb${NB_MIN}-${NB_MAX}_th1_r${rep}"
    local log="logs/${CONFIG}_core${CORE}_ssrfb_size${SIZE}_th1_r${rep}.log"

    hpc_meta > "${base}_meta.txt"
    {
        echo
        echo "# === NoFlush ==="
        echo "NoFlush: ${NOFLUSH_PATH}"
        ldd "${NOFLUSH_PATH}" | grep -E 'plasma|openblas|mkl|gomp|iomp'
        echo
        echo "# === 計測条件 ==="
        echo "CORE=${CORE} NODE=${NODE} SIZE=${SIZE} STEP=${STEP} NB=${NB_MIN}-${NB_MAX} N_IT=${N_IT} REP=${rep}"
        echo "numactl ${NUMACTL_ARGS[*]}"
        echo "PBS_JOBID=${PBS_JOBID:-（なし）}"
    } >> "${base}_meta.txt"
    hpc_snapshot "開始" | tee -a "${base}_meta.txt"

    start_sampler "${base}_clock.txt"
    echo "===== sweep start (r${rep}) ====="
    SECONDS=0
    numactl "${NUMACTL_ARGS[@]}" \
        "${PY}" -m plasma_perf bench ssrfb \
            --exe "${NOFLUSH_PATH}" \
            --size "${SIZE}" --threads 1 \
            --nb-min "${NB_MIN}" --nb-max "${NB_MAX}" --step "${STEP}" \
            --trials 1 --n-it "${N_IT}" \
            --output "${base}.csv" \
        2>&1 | tee "${log}"
    echo "===== sweep end (r${rep}, ${SECONDS} sec) ====="
    stop_sampler

    hpc_snapshot "終了" | tee -a "${base}_meta.txt"
    [ -f "${base}.csv" ] && echo "rows: $(( $(wc -l < "${base}.csv") - 1 ))"
}

IFS=: read -r -a REP_LIST <<< "${REPS}"
for rep in "${REP_LIST[@]}"; do
    run_rep "${rep}"
done
