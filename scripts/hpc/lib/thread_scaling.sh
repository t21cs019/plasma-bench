#!/usr/bin/env bash
# =============================================================================
# thread_scaling.sh — 同じ機械で、スレッド数だけを変えて tileQR を測る
#
#   「コアを増やすと小さい nb で遅くなる」「タスク1個ごとの時間 τ がコア数に比例する」を、
#   機種の違いなしで確かめる。1点ごとにスレッド数を入れ替え、反復ごとに順番をずらす
#   （時間による変動をスレッド数の間でそろえるため）。plasmatest は1点ごとに新しいプロセス。
#
#   使い方:
#       MACHINE=epyc7543 THREADS="4 8 16 32" SIZES="4096 8192" NB_LIST="32 64 128" \
#           bash scripts/hpc/lib/thread_scaling.sh
#
#   変数:
#       MACHINE  機種（必須）
#       THREADS  スレッド数の並び（既定は machine.sh の MACHINE_P）
#       SIZES    行列サイズの並び（必須）
#       NB_LIST  nb の並び（必須）
#       IB_DIV   ib = nb / IB_DIV（既定 4）
#       REPS     反復数（既定 5）
#       BIND     close | none（既定 close）
#       MEM      bind | interleave（既定 bind）
#       TIMEOUT  1点の上限秒（既定 1800。超えた点は GFlops=0 の欠測になる）
#
#   結果: results/benchmark/<config>_thread-scaling_<時刻>/
#       thread_scaling.csv（threads,size,nb,ib,GFlops。1行 = 1回）
#       affinity_p<P>.txt / meta.txt / run.log / summary.txt
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

THREADS=${THREADS:-${MACHINE_P}}
SIZES=${SIZES:?SIZES を指定すること（例 SIZES="4096 8192"）}
NB_LIST=${NB_LIST:?NB_LIST を指定すること（例 NB_LIST="32 64 128"）}
IB_DIV=${IB_DIV:-4}
REPS=${REPS:-5}
BIND=${BIND:-close}
MEM=${MEM:-bind}
TIMEOUT=${TIMEOUT:-1800}

hpc_setup
read -r -a THREAD_LIST <<< "${THREADS}"
OUTDIR="results/benchmark/${CONFIG}_thread-scaling_$(date +%Y%m%d_%H%M%S)"
CSV="${OUTDIR}/thread_scaling.csv"
mkdir -p "${OUTDIR}"

echo "===== preflight ====="
MAX_P=$(printf '%s\n' "${THREAD_LIST[@]}" | sort -n | tail -n 1)
hpc_select_nodes "${MAX_P}"
hpc_meta > "${OUTDIR}/meta.txt"
{
    echo
    echo "# === 計測条件 ==="
    echo "THREADS=${THREADS} SIZES=${SIZES} NB_LIST=${NB_LIST} IB_DIV=${IB_DIV} REPS=${REPS}"
    echo "BIND=${BIND} MEM=${MEM} TIMEOUT=${TIMEOUT}"
} >> "${OUTDIR}/meta.txt"
for P in "${THREAD_LIST[@]}"; do
    hpc_select_nodes "${P}"
    hpc_set_cond "${BIND}" "${MEM}"
    hpc_check_cores "${P}"
    hpc_check_affinity "${P}" "${BIND}" "${OUTDIR}/affinity_p${P}.txt"
done
echo "output: ${OUTDIR}"
hpc_snapshot "開始" | tee -a "${OUTDIR}/meta.txt"

echo "===== thread scaling start ====="
SECONDS=0
np=${#THREAD_LIST[@]}
for rep in $(seq 1 "${REPS}"); do
    for size in ${SIZES}; do
        for nb in ${NB_LIST}; do
            ib=$(( nb / IB_DIV ))
            for i in $(seq 0 $(( np - 1 ))); do
                P=${THREAD_LIST[$(( (i + rep) % np ))]}
                hpc_select_nodes "${P}"
                hpc_set_cond "${BIND}" "${MEM}"
                numactl "${NUMACTL_ARGS[@]}" \
                    "${PY}" -m plasma_perf bench tileqr \
                        --size "${size}" --threads "${P}" \
                        --nb-min "${nb}" --nb-max "${nb}" \
                        --ib-min "${ib}" --ib-max "${ib}" \
                        --trials 1 --timeout-sec "${TIMEOUT}" \
                        --output "${CSV}" \
                    >> "${OUTDIR}/run.log" 2>&1
                gf=$(tail -n 1 "${CSV}" | cut -d, -f5 | tr -d '\r')
                printf "rep %d  size %5d  nb %3d  ib %3d  p %3d  %8s GFlops  (%d sec)\n" \
                    "${rep}" "${size}" "${nb}" "${ib}" "${P}" "${gf}" "${SECONDS}"
            done
        done
    done
done
echo "===== thread scaling end (${SECONDS} sec) ====="
hpc_snapshot "終了" | tee -a "${OUTDIR}/meta.txt"

hpc_summary "${CSV}" GFlops size nb threads | tee "${OUTDIR}/summary.txt"
