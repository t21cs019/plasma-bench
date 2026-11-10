#!/usr/bin/env bash
# =============================================================================
# task_overhead.sh — 中身のないタスクをタイル QR の依存関係で流し、タスク1個あたりの時間 τ を測る
#
#   QR の計算を使わずに a（= τ / コア数）を決めるための計測。プログラムは native/task_overhead/。
#   1点ごとにスレッド数とランタイムを入れ替え、反復ごとに順番をずらす。
#
#   使い方:
#       MACHINE=epyc7543 THREADS="1 2 4 8 16 32" TILES="64 128" bash scripts/hpc/lib/task_overhead.sh
#
#   変数:
#       MACHINE   機種（必須）
#       THREADS   スレッド数の並び（既定は machine.sh の MACHINE_P）
#       TILES     タイル数の並び（必須。タスク数はおよそ タイル数^3 / 3）
#       WORK_NS   タスクの中で待つ時間 [ns] の並び（既定 0 = 空のタスク）
#       RUNTIMES  gomp | iomp5 の並び（既定は両方。ビルドできなかったものは飛ばす）
#       REPS      プロセス数（既定 5）
#       ITERS     1プロセスの中で繰り返す回数（既定 3）
#       BIND      close | none（既定 close）
#       MEM       bind | interleave（既定 bind）
#       TIMEOUT   1プロセスの上限秒（既定 1800）
#
#   結果: results/benchmark/<config>_task-overhead_<時刻>/
#       task_overhead.csv（process,runtime,threads,tiles,ntasks,work_ns,iter,total_sec,submit_sec,tau_us）
#       affinity_<runtime>_p<P>.txt / meta.txt / run.log / summary.txt
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

THREADS=${THREADS:-${MACHINE_P}}
TILES=${TILES:?TILES を指定すること（例 TILES="64 128"）}
WORK_NS=${WORK_NS:-0}
RUNTIMES=${RUNTIMES:-"gomp iomp5"}
REPS=${REPS:-5}
ITERS=${ITERS:-3}
BIND=${BIND:-close}
MEM=${MEM:-bind}
TIMEOUT=${TIMEOUT:-1800}

hpc_setup
SRC_DIR="${REPO}/native/task_overhead"
make -C "${SRC_DIR}" PLASMA_TEST="${PLASMA_TEST}"

RT_LIST=()
for rt in ${RUNTIMES}; do
    if [ -x "${SRC_DIR}/build/task_overhead_${rt}" ]; then
        RT_LIST+=("${rt}")
    else
        echo "[note] ${rt} 版がビルドされていないので飛ばす"
    fi
done
[ "${#RT_LIST[@]}" -gt 0 ] || { echo "[ERROR] 実行できるプログラムがありません"; exit 1; }

read -r -a THREAD_LIST <<< "${THREADS}"
OUTDIR="results/benchmark/${CONFIG}_task-overhead_$(date +%Y%m%d_%H%M%S)"
CSV="${OUTDIR}/task_overhead.csv"
mkdir -p "${OUTDIR}"
echo "process,runtime,threads,tiles,ntasks,work_ns,iter,total_sec,submit_sec,tau_us" > "${CSV}"

# スレッドの固定の検査。プログラムが "# affinity threads=T distinct_sets=D" を出す
check_program_affinity() {
    local exe=$1 p=$2 out=$3 thr nset
    OMP_NUM_THREADS="${p}" numactl "${NUMACTL_ARGS[@]}" "${exe}" --tiles 8 --iters 1 > "${out}" 2>&1
    thr=$(awk '$2 == "affinity" {sub("threads=", "", $3); print $3}' "${out}")
    nset=$(awk '$2 == "affinity" {sub("distinct_sets=", "", $4); print $4}' "${out}")
    echo "affinity (${exe##*_}, bind=${BIND}): threads=${thr}, 異なるコア集合=${nset}, P=${p}"
    if [ "${thr}" != "${p}" ]; then
        echo "[ERROR] スレッド数が P と違う。${out} を確認すること"; exit 1
    fi
    if [ "${BIND}" = close ] && [ "${nset}" != "${p}" ]; then
        echo "[ERROR] スレッドが1コアずつに固定されていない。${out} を確認すること"; exit 1
    fi
}

echo "===== preflight ====="
MAX_P=$(printf '%s\n' "${THREAD_LIST[@]}" | sort -n | tail -n 1)
hpc_select_nodes "${MAX_P}"
hpc_meta > "${OUTDIR}/meta.txt"
{
    echo
    echo "# === 計測条件 ==="
    echo "THREADS=${THREADS} TILES=${TILES} WORK_NS=${WORK_NS} RUNTIMES=${RT_LIST[*]}"
    echo "REPS=${REPS} ITERS=${ITERS} BIND=${BIND} MEM=${MEM} TIMEOUT=${TIMEOUT}"
    for rt in "${RT_LIST[@]}"; do
        echo "# ldd task_overhead_${rt}"
        ldd "${SRC_DIR}/build/task_overhead_${rt}" | grep -E 'gomp|iomp'
    done
} >> "${OUTDIR}/meta.txt"
for rt in "${RT_LIST[@]}"; do
    exe="${SRC_DIR}/build/task_overhead_${rt}"
    for P in "${THREAD_LIST[@]}"; do
        hpc_select_nodes "${P}"
        hpc_set_cond "${BIND}" "${MEM}" "${exe}"
        hpc_check_cores "${P}"
        check_program_affinity "${exe}" "${P}" "${OUTDIR}/affinity_${rt}_p${P}.txt"
    done
done
echo "output: ${OUTDIR}"
hpc_snapshot "開始" | tee -a "${OUTDIR}/meta.txt"

echo "===== task overhead start ====="
SECONDS=0
np=${#THREAD_LIST[@]}
nrt=${#RT_LIST[@]}
for rep in $(seq 1 "${REPS}"); do
    for tiles in ${TILES}; do
        for work in ${WORK_NS}; do
            for i in $(seq 0 $(( np - 1 ))); do
                P=${THREAD_LIST[$(( (i + rep) % np ))]}
                for j in $(seq 0 $(( nrt - 1 ))); do
                    rt=${RT_LIST[$(( (j + rep) % nrt ))]}
                    exe="${SRC_DIR}/build/task_overhead_${rt}"
                    hpc_select_nodes "${P}"
                    hpc_set_cond "${BIND}" "${MEM}" "${exe}"
                    out=$(OMP_NUM_THREADS="${P}" timeout "${TIMEOUT}" \
                        numactl "${NUMACTL_ARGS[@]}" "${exe}" \
                            --tiles "${tiles}" --work-ns "${work}" --iters "${ITERS}" 2>&1) \
                        || echo "[warn] 異常終了: ${rt} p=${P} tiles=${tiles} work=${work}" \
                            | tee -a "${OUTDIR}/run.log"
                    echo "${out}" >> "${OUTDIR}/run.log"
                    echo "${out}" | awk -v p="${rep}" '/^(libgomp|libiomp5),/ {print p "," $0}' >> "${CSV}"
                    tau=$(echo "${out}" | awk -F, '/^(libgomp|libiomp5),/ {printf "%s ", $NF}')
                    printf "rep %d  tiles %4d  work %5d ns  %-5s  p %3d  tau %s us  (%d sec)\n" \
                        "${rep}" "${tiles}" "${work}" "${rt}" "${P}" "${tau}" "${SECONDS}"
                done
            done
        done
    done
done
echo "===== task overhead end (${SECONDS} sec) ====="
hpc_snapshot "終了" | tee -a "${OUTDIR}/meta.txt"

hpc_summary "${CSV}" tau_us work_ns tiles runtime threads | tee "${OUTDIR}/summary.txt"
