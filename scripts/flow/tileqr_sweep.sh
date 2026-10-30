#!/usr/bin/env bash
# =============================================================================
# tileqr_sweep.sh — 不老・弐 Type I で tileQR の nb・ib スイープを1条件・1反復ぶん流す
#
#   1 ジョブ = 1 条件 × 1 反復（--trials 1）。AOBA と同じく、反復はジョブを分けて稼ぐ。
#   既定はスレッド固定あり（close）・--membind。bind_ab.sh の結果で決めた条件を指定する。
#
#   使い方（ジョブスクリプトから。変数で条件を渡す）:
#       P=192 SIZE=4096 REP=1 bash scripts/flow/tileqr_sweep.sh
#       P=48  SIZE=2048 REP=2 MEM=interleave bash scripts/flow/tileqr_sweep.sh
#
#   変数:
#       P       スレッド数 = 物理コア数（既定 192 = 1ソケット）
#       SIZE    行列サイズ（必須）
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
SIZE=${SIZE:?SIZE を指定すること（例 SIZE=4096）}
REP=${REP:-1}
BIND=${BIND:-close}
MEM=${MEM:-bind}
if [ "${SIZE}" -le 4096 ]; then STEP=${STEP:-4}; else STEP=${STEP:-8}; fi
NB_MIN=${NB_MIN:-32}
NB_MAX=${NB_MAX:-512}

flow_setup
flow_select_nodes "${P}"
flow_set_cond "${BIND}" "${MEM}"
TAG=$(flow_cond_tag "${BIND}" "${MEM}")
BASE="results/benchmark/${CONFIG}_${TAG}_tileqr_size${SIZE}_nb${NB_MIN}-${NB_MAX}_th${P}_r${REP}"
LOG="logs/${CONFIG}_${TAG}_tileqr_size${SIZE}_th${P}_r${REP}.log"

echo "===== preflight ====="
flow_meta > "${BASE}_meta.txt"
{
    echo
    echo "# === 計測条件 ==="
    echo "P=${P} SIZE=${SIZE} STEP=${STEP} NB=${NB_MIN}-${NB_MAX} REP=${REP}"
    echo "OMP_PLACES=${OMP_PLACES:-（なし）} OMP_PROC_BIND=${OMP_PROC_BIND:-（なし）}"
    echo "numactl ${NUMACTL_ARGS[*]}"
} >> "${BASE}_meta.txt"
flow_check_cores "${P}"
flow_check_affinity "${P}" "${BIND}" "${BASE}_affinity.txt"
flow_snapshot "開始" | tee -a "${BASE}_meta.txt"

echo "===== sweep start ====="
SECONDS=0
numactl "${NUMACTL_ARGS[@]}" \
    "${PY}" -m plasma_perf bench tileqr \
        --size "${SIZE}" --threads "${P}" \
        --nb-min "${NB_MIN}" --nb-max "${NB_MAX}" --step "${STEP}" \
        --trials 1 \
        --output "${BASE}.csv" \
    2>&1 | tee "${LOG}"
echo "===== sweep end (${SECONDS} sec) ====="

# 終了時の free が開始時より大きく減っていれば、他のジョブが同じノードで動いていた可能性がある
flow_snapshot "終了" | tee -a "${BASE}_meta.txt"
[ -f "${BASE}.csv" ] && echo "rows: $(( $(wc -l < "${BASE}.csv") - 1 ))"
