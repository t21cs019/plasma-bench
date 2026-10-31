#!/usr/bin/env bash
# =============================================================================
# tileqr_sweep.sh — 不老・弐 Type I で tileQR の nb・ib スイープを1反復ぶん流す
#
#   1 ジョブ = 1 反復（--trials 1）。AOBA と同じく、反復はジョブを分けて稼ぐ。
#   SIZE に複数のサイズを ":" でつなぐと、同じジョブの中で順に流す（例 SIZE=1024:2048）。
#   短いサイズを1ジョブにまとめ、同時に流せるジョブ数（t1-single は1人20本）に収めるため。
#
#   既定はスレッド固定あり（close）・--membind。2026-10-31 の A/B テスト（bind_ab.sh、p=192）で、
#   close は固定なしより最適 nb の付近で 7〜46% 速く、ばらつきも小さかった。
#   --membind と --interleave の差はばらつきの範囲だった。
#
#   使い方（ジョブスクリプトから。変数で条件を渡す）:
#       P=192 SIZE=4096 REP=1 bash scripts/flow/tileqr_sweep.sh
#       SIZE=1024:2048 REP=3 bash scripts/flow/tileqr_sweep.sh
#
#   変数:
#       P       スレッド数 = 物理コア数（既定 192 = 1ソケット）
#       SIZE    行列サイズ（必須）。":" 区切りで複数
#       REP     反復の番号（既定 1。ファイル名にだけ使う）
#       BIND    close | none（既定 close）
#       MEM     bind | interleave（既定 bind）
#       STEP    nb・ib の刻み（既定: size<=4096 は 4、それ以上は 8。plan.yaml の規約）
#       NB_MIN / NB_MAX（既定 32 / 512）
#
#   結果: results/benchmark/<config>_<bind>_tileqr_size<S>_nb<min>-<max>_th<P>_r<REP>.csv
#         同じ名前の _meta.txt（計測条件）と _affinity.txt（スレッド固定の検査）
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

P=${P:-192}
SIZE=${SIZE:?SIZE を指定すること（例 SIZE=4096、SIZE=1024:2048）}
REP=${REP:-1}
BIND=${BIND:-close}
MEM=${MEM:-bind}
NB_MIN=${NB_MIN:-32}
NB_MAX=${NB_MAX:-512}

flow_setup
flow_select_nodes "${P}"
flow_set_cond "${BIND}" "${MEM}"
TAG=$(flow_cond_tag "${BIND}" "${MEM}")

# 1サイズぶんのスイープ
run_size() {
    local size=$1
    local step
    if [ -n "${STEP:-}" ]; then
        step=${STEP}
    elif [ "${size}" -le 4096 ]; then
        step=4
    else
        step=8
    fi
    local base="results/benchmark/${CONFIG}_${TAG}_tileqr_size${size}_nb${NB_MIN}-${NB_MAX}_th${P}_r${REP}"
    local log="logs/${CONFIG}_${TAG}_tileqr_size${size}_th${P}_r${REP}.log"

    echo "===== preflight (size ${size}) ====="
    flow_meta > "${base}_meta.txt"
    {
        echo
        echo "# === 計測条件 ==="
        echo "P=${P} SIZE=${size} STEP=${step} NB=${NB_MIN}-${NB_MAX} REP=${REP}"
        echo "OMP_PLACES=${OMP_PLACES:-（なし）} OMP_PROC_BIND=${OMP_PROC_BIND:-（なし）} KMP_AFFINITY=${KMP_AFFINITY:-（なし）}"
        echo "numactl ${NUMACTL_ARGS[*]}"
        echo "PBS_JOBID=${PBS_JOBID:-（なし）}"
    } >> "${base}_meta.txt"
    flow_check_cores "${P}"
    flow_check_affinity "${P}" "${BIND}" "${base}_affinity.txt"
    flow_snapshot "開始" | tee -a "${base}_meta.txt"

    echo "===== sweep start (size ${size}) ====="
    SECONDS=0
    numactl "${NUMACTL_ARGS[@]}" \
        "${PY}" -m plasma_perf bench tileqr \
            --size "${size}" --threads "${P}" \
            --nb-min "${NB_MIN}" --nb-max "${NB_MAX}" --step "${step}" \
            --trials 1 \
            --output "${base}.csv" \
        2>&1 | tee "${log}"
    echo "===== sweep end (size ${size}, ${SECONDS} sec) ====="

    # 終了時の free が開始時より大きく減っていれば、他のジョブが同じノードで動いていた可能性がある
    flow_snapshot "終了" | tee -a "${base}_meta.txt"
    [ -f "${base}.csv" ] && echo "rows: $(( $(wc -l < "${base}.csv") - 1 ))"
}

IFS=: read -r -a SIZE_LIST <<< "${SIZE}"
for size in "${SIZE_LIST[@]}"; do
    run_size "${size}"
done
