#!/usr/bin/env bash
# =============================================================================
# submit_campaign.sh — 不老・弐 Type I の本番計測（p=192、close・membind）をまとめて投入する
#
#   計画（2026-10-31）: 1024 / 2048 / 4096 / 8192 を各5反復、16384 を1反復。
#   1反復 = 1ジョブ = 1ノード（AOBA と同じく、反復は別のノードで取る）。
#   t1-single は1人20本までなので、短い 1024 と 2048 は同じジョブで続けて流し、全部で16本にする。
#
#     ジョブ                 本数  見積もり   walltime
#     size 16384             1     1〜2.5 日  120 時間
#     size 8192              5     7〜8 時間  18 時間
#     size 4096              5     4〜6 時間  12 時間
#     size 1024 → 2048       5     1〜2 時間  6 時間
#
#   見積もりは A/B テスト（225 点で 43 分）からの外挿。1点あたりの時間は、分解そのものより
#   plasmatest の行列生成と検算（--test=y）が長い。walltime を超えても nb ごとに CSV は残るので、
#   続きは NB_MIN を指定して流し直す。
#
#   使い方（ログインノードで、plasma-bench の直下から）:
#       bash scripts/flow/submit_campaign.sh            # 投入するコマンドを表示するだけ
#       bash scripts/flow/submit_campaign.sh --submit   # 実際に qsub する
# =============================================================================
set -euo pipefail

SUBMIT=0
[ "${1:-}" = "--submit" ] && SUBMIT=1

FLOW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB="${FLOW_DIR}/job_sweep.sh"

# <SIZE（":" で複数）> <反復の番号> <walltime>。長いものから投入する
PLAN=(
    "16384     1         120:00:00"
    "8192      1,2,3,4,5 18:00:00"
    "4096      1,2,3,4,5 12:00:00"
    "1024:2048 1,2,3,4,5 06:00:00"
)

if [ "${SUBMIT}" -eq 1 ]; then
    command -v qsub > /dev/null || { echo "[ERROR] qsub が見つかりません（ログインノードで実行すること）"; exit 1; }
fi

njobs=0
for row in "${PLAN[@]}"; do
    read -r size reps walltime <<< "${row}"
    IFS=, read -r -a rep_list <<< "${reps}"
    for rep in "${rep_list[@]}"; do
        name="tq_s${size//:/-}_r${rep}"
        cmd=(qsub -N "${name}" -l "walltime=${walltime}" -v "SIZE=${size},REP=${rep}" "${JOB}")
        echo "${cmd[*]}"
        if [ "${SUBMIT}" -eq 1 ]; then
            "${cmd[@]}"
        fi
        njobs=$(( njobs + 1 ))
    done
done

echo "jobs: ${njobs}"
if [ "${SUBMIT}" -eq 0 ]; then
    echo "（表示だけ。投入するには --submit を付ける）"
fi
