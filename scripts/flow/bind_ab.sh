#!/usr/bin/env bash
# =============================================================================
# bind_ab.sh — OpenMP のスレッド固定で tileQR の性能が変わるかを確かめる（不老・弐 Type I）
#
#   本番のスイープの前に、t1-debug（1時間まで）で1回流す。
#   3つの条件を、同じジョブの中で1点ずつ交互に流す（時間による変動を条件間でそろえるため）。
#   反復ごとに条件の順番をずらす。
#
#     none:bind         固定なし、--membind     （これまでの計測と同じ条件）
#     close:bind        OMP_PLACES=cores OMP_PROC_BIND=close、--membind
#     close:interleave  同上、--interleave      （メモリを NUMA ノードに均等に置く）
#
#   見るもの:
#     * 固定なし と close の平均 GFlops の差。小さい nb ほど差が大きければ、
#       多コアで小さい nb が遅くなる損失 M(nb) の一部はスレッドの移動が原因
#     * 反復ごとのばらつき（cv）が close で小さくなるか
#     * --membind と --interleave の差。差が無ければ本番は AOBA と同じ --membind
#
#   使い方（ジョブスクリプトから）:
#       bash scripts/flow/bind_ab.sh
#       P=96 SIZES="4096" REPS=3 bash scripts/flow/bind_ab.sh
#
#   結果: results/benchmark/flow_bind-ab_<時刻>/
#       <条件>_size<S>.csv  条件ごとの CSV（1行 = 1回）
#       affinity_<条件>.txt  スレッドの固定の検査の出力
#       meta.txt / run.log / summary.txt
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

P=${P:-192}
SIZES=${SIZES:-"2048 4096 8192"}
NB_LIST=${NB_LIST:-"64 128 192 256 384"}
IB=${IB:-32}
REPS=${REPS:-5}
CONDS=(none:bind close:bind close:interleave)

flow_setup
flow_select_nodes "${P}"
OUTDIR="results/benchmark/flow_bind-ab_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${OUTDIR}"

echo "===== preflight ====="
flow_meta > "${OUTDIR}/meta.txt"
for c in "${CONDS[@]}"; do
    flow_set_cond "${c%%:*}" "${c##*:}"
    flow_check_cores "${P}"
    flow_check_affinity "${P}" "${c%%:*}" "${OUTDIR}/affinity_$(flow_cond_tag "${c%%:*}" "${c##*:}").txt"
done
echo "P=${P} / sizes: ${SIZES} / nb: ${NB_LIST} / ib=${IB} / reps=${REPS}"
echo "output: ${OUTDIR}"
flow_snapshot "開始" | tee -a "${OUTDIR}/meta.txt"

echo "===== A/B start ====="
SECONDS=0
ncond=${#CONDS[@]}
for rep in $(seq 1 "${REPS}"); do
    for size in ${SIZES}; do
        for nb in ${NB_LIST}; do
            for i in $(seq 0 $(( ncond - 1 ))); do
                c=${CONDS[$(( (i + rep) % ncond ))]}
                flow_set_cond "${c%%:*}" "${c##*:}"
                tag=$(flow_cond_tag "${c%%:*}" "${c##*:}")
                numactl "${NUMACTL_ARGS[@]}" \
                    "${PY}" -m plasma_perf bench tileqr \
                        --size "${size}" --threads "${P}" \
                        --nb-min "${nb}" --nb-max "${nb}" \
                        --ib-min "${IB}" --ib-max "${IB}" \
                        --trials 1 \
                        --output "${OUTDIR}/${tag}_size${size}.csv" \
                    >> "${OUTDIR}/run.log" 2>&1
                gf=$(tail -n 1 "${OUTDIR}/${tag}_size${size}.csv" | cut -d, -f5)
                printf "rep %d  size %5d  nb %3d  %-18s %8s GFlops\n" "${rep}" "${size}" "${nb}" "${tag}" "${gf}"
            done
        done
    done
done
echo "===== A/B end (${SECONDS} sec) ====="
flow_snapshot "終了" | tee -a "${OUTDIR}/meta.txt"

# --- 条件 × size × nb ごとの平均・最小・最大・変動係数 ------------------------
{
    printf "%-18s %5s %4s %2s %9s %9s %9s %6s\n" cond size nb n mean min max cv%
    for f in "${OUTDIR}"/bind-*_size*.csv; do
        tag=$(basename "${f}" | sed 's/_size.*//')
        awk -F, -v tag="${tag}" 'NR > 1 {
                k = $2 " " $3; n[k]++; s[k] += $5; ss[k] += $5 * $5
                if (!(k in mn) || $5 < mn[k]) mn[k] = $5
                if (!(k in mx) || $5 > mx[k]) mx[k] = $5
            }
            END {
                for (k in n) {
                    m = s[k] / n[k]; v = ss[k] / n[k] - m * m; if (v < 0) v = 0
                    split(k, a, " ")
                    printf "%-18s %5d %4d %2d %9.1f %9.1f %9.1f %6.1f\n",
                        tag, a[1], a[2], n[k], m, mn[k], mx[k], (m > 0 ? 100 * sqrt(v) / m : 0)
                }
            }' "${f}"
    done | sort -k2,2n -k3,3n -k1,1
} | tee "${OUTDIR}/summary.txt"
