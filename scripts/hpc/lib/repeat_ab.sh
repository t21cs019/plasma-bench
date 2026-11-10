#!/usr/bin/env bash
# =============================================================================
# repeat_ab.sh — 同じプロセスで QR を複数回行い、1回目と2回目以降の差を測る（plasmatest --repeat）
#
#   スレッド固定なしの損失が、起動直後だけのものか、定常的なもの（L3 など）かを分ける。
#   2回目以降で固定あり・なしの差が消えれば起動直後の損失、残れば定常的な損失と読む。
#
#   plasma_perf の bench は plasmatest の最初の1行しか読まないので、ここでは plasmatest を
#   直接呼び、生の出力を raw/ に残して CSV に直す。
#
#   使い方:
#       MACHINE=epyc7543 SIZES="2048 4096" NB_LIST="192 256" bash scripts/hpc/lib/repeat_ab.sh
#
#   変数:
#       MACHINE  機種（必須）
#       P        スレッド数（既定は machine.sh の MACHINE_P）
#       SIZES    行列サイズの並び（必須）
#       NB_LIST  nb の並び（必須）
#       IB       ib（既定 32。bind_ab.sh と同じ）
#       REPEAT   1プロセスの中で分解する回数（既定 3）
#       PROCS    条件ごとのプロセス数（既定 5）
#       BINDS    固定の条件の並び（既定 "none close"）
#       MEM      bind | interleave（既定 bind）
#       TIMEOUT  1プロセスの上限秒（既定 1800）
#
#   結果: results/benchmark/<config>_repeat-ab_<時刻>/
#       repeat_ab.csv（bind,threads,size,nb,ib,process,iter,Time_sec,GFlops。iter=1 が最初の分解）
#       raw/ / affinity_<bind>.txt / meta.txt / run.log / summary.txt
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

P=${P:-${MACHINE_P}}
SIZES=${SIZES:?SIZES を指定すること（例 SIZES="2048 4096"）}
NB_LIST=${NB_LIST:?NB_LIST を指定すること（例 NB_LIST="192 256"）}
IB=${IB:-32}
REPEAT=${REPEAT:-3}
PROCS=${PROCS:-5}
BINDS=${BINDS:-"none close"}
MEM=${MEM:-bind}
TIMEOUT=${TIMEOUT:-1800}

hpc_setup
hpc_select_nodes "${P}"
read -r -a BIND_LIST <<< "${BINDS}"
OUTDIR="results/benchmark/${CONFIG}_repeat-ab_$(date +%Y%m%d_%H%M%S)"
CSV="${OUTDIR}/repeat_ab.csv"
mkdir -p "${OUTDIR}/raw"
echo "bind,threads,size,nb,ib,process,iter,Time_sec,GFlops" > "${CSV}"

echo "===== preflight ====="
hpc_meta > "${OUTDIR}/meta.txt"
{
    echo
    echo "# === 計測条件 ==="
    echo "P=${P} SIZES=${SIZES} NB_LIST=${NB_LIST} IB=${IB} REPEAT=${REPEAT} PROCS=${PROCS}"
    echo "BINDS=${BINDS} MEM=${MEM} TIMEOUT=${TIMEOUT}"
} >> "${OUTDIR}/meta.txt"
for b in "${BIND_LIST[@]}"; do
    hpc_set_cond "${b}" "${MEM}"
    hpc_check_cores "${P}"
    hpc_check_affinity "${P}" "${b}" "${OUTDIR}/affinity_${b}.txt"
done
echo "output: ${OUTDIR}"
hpc_snapshot "開始" | tee -a "${OUTDIR}/meta.txt"

echo "===== repeat A/B start ====="
SECONDS=0
nbind=${#BIND_LIST[@]}
for proc in $(seq 1 "${PROCS}"); do
    for size in ${SIZES}; do
        for nb in ${NB_LIST}; do
            for i in $(seq 0 $(( nbind - 1 ))); do
                b=${BIND_LIST[$(( (i + proc) % nbind ))]}
                hpc_set_cond "${b}" "${MEM}"
                raw="${OUTDIR}/raw/${b}_size${size}_nb${nb}_proc${proc}.txt"
                OMP_NUM_THREADS="${P}" timeout "${TIMEOUT}" \
                    numactl "${NUMACTL_ARGS[@]}" "${PLASMA_TEST}" dgeqrf \
                        --dim="${size}x${size}" --nb="${nb}" --ib="${IB}" --repeat="${REPEAT}" \
                    > "${raw}" 2>&1 || echo "[warn] 異常終了: ${raw}" | tee -a "${OUTDIR}/run.log"
                awk -v prefix="${b},${P},${size},${nb},${IB},${proc}" \
                    '$1 == "pass" {printf "%s,%d,%s,%s\n", prefix, ++i, $3, $4}' "${raw}" >> "${CSV}"
                got=$(awk '$1 == "pass" {n++} END {print n + 0}' "${raw}")
                [ "${got}" -eq "${REPEAT}" ] \
                    || echo "[warn] pass の行が ${got} 行（期待 ${REPEAT} 行）: ${raw}" | tee -a "${OUTDIR}/run.log"
                gfs=$(awk '$1 == "pass" {printf "%s ", $4}' "${raw}")
                printf "proc %d  size %5d  nb %3d  %-5s  %s GFlops  (%d sec)\n" \
                    "${proc}" "${size}" "${nb}" "${b}" "${gfs}" "${SECONDS}"
            done
        done
    done
done
echo "===== repeat A/B end (${SECONDS} sec) ====="
hpc_snapshot "終了" | tee -a "${OUTDIR}/meta.txt"

{
    hpc_summary "${CSV}" Time_sec size nb bind iter
    echo
    hpc_summary "${CSV}" GFlops size nb bind iter
} | tee "${OUTDIR}/summary.txt"
