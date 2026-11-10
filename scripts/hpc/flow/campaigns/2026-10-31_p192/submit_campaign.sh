#!/usr/bin/env bash
# =============================================================================
# submit_campaign.sh — 不老・弐 Type I の本番計測（p=192、close・membind）のうち、
#                      size 1024〜8192 を投入する（size 16384 は submit_16384.sh）
#
#   計画（2026-10-31）: 1024 / 2048 / 4096 / 8192 を各5反復、nb 32〜512 の全域。
#   1反復 = 1ジョブ = 1ノード（AOBA と同じく、反復は別のノードで取る）。
#   1024 と 2048 は同じジョブで続けて流す（t1-single は1人20本まで同時に実行できるため、
#   16384 の5本と合わせて20本に収める）。小さいサイズから投入する。
#
#     ジョブ                 本数  見積もり   walltime
#     size 1024 → 2048       5     1〜2 時間  6 時間
#     size 4096              5     4〜6 時間  12 時間
#     size 8192              5     7〜8 時間  18 時間
#
#   見積もりは A/B テスト（225 点で 43 分）からの外挿。1点あたりの時間は、分解そのものより
#   plasmatest の行列生成と検算（--test=y）が長い。walltime を超えても nb ごとに CSV は残るので、
#   続きは NB_MIN を指定して流し直す。FLOW_MAINT を設定すると、walltime をメンテナンスの前に収まるよう縮める。
#
#   使い方（ログインノードで、plasma-bench の直下から）:
#       bash scripts/hpc/flow/campaigns/2026-10-31_p192/submit_campaign.sh            # 投入するコマンドを表示するだけ
#       bash scripts/hpc/flow/campaigns/2026-10-31_p192/submit_campaign.sh --submit   # 実際に qsub する
# =============================================================================
set -euo pipefail
# 次のメンテナンスが決まったら、walltime がかからないように日時を設定する（設定しなければ縮めない）
# FLOW_MAINT="YYYY-MM-DD HH:MM +0900"
source "$(dirname "${BASH_SOURCE[0]}")/../../submit_lib.sh"

SUBMIT=0
[ "${1:-}" = "--submit" ] && SUBMIT=1

# <SIZE（":" で複数）> <反復の番号> <walltime> [追加の変数（"," 区切り）]。小さいものから投入する
PLAN=(
    "1024:2048 1,2,3,4,5 06:00:00"
    "4096      1,2,3,4,5 12:00:00"
    "8192      1,2,3,4,5 18:00:00"
)

flow_submit_plan "${SUBMIT}"
