#!/usr/bin/env bash
# =============================================================================
# submit_16384.sh — 不老・弐 Type I で size 16384 を nb 32〜512 の全域、5反復ぶん投入する
#
#   全域（1022 点）を1ジョブで流すと 24〜53 時間かかる見込みで、メンテナンス
#   （2026-11-02 08:00）に間に合わないおそれがある。また plasmatest は nb の小さい順に流れるので、
#   途中で止まると最適 nb の付近（これまでの機種で 360〜504）が欠ける。
#   そこで nb の範囲を3つに分け、大きい nb から投入する。3つをつなぐと全域（8 刻み）になる。
#
#     段階  nb の範囲   点数  見積もり                  本数  walltime
#     high  392〜512    448   11〜23 時間               5     30 時間
#     rest  256〜384    336   8〜17 時間                5     24 時間
#     rest  32〜248     238   6〜12 時間 + 小さい nb の分  5     24 時間
#
#   high は submit_campaign.sh と同時に投入する（合わせて20本 = t1-single の同時実行の上限）。
#   rest は submit_campaign.sh のジョブが終わって枠が空いてから投入する。
#   walltime はメンテナンスの前に収まるよう自動で縮める。
#
#   使い方（ログインノードで、plasma-bench の直下から）:
#       bash scripts/flow/submit_16384.sh high              # 表示だけ
#       bash scripts/flow/submit_16384.sh high --submit     # 投入
#       bash scripts/flow/submit_16384.sh rest --submit
# =============================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/submit_lib.sh"

STAGE=${1:-}
SUBMIT=0
[ "${2:-}" = "--submit" ] && SUBMIT=1

case "${STAGE}" in
    high)
        PLAN=(
            "16384 1,2,3,4,5 30:00:00 NB_MIN=392,NB_MAX=512"
        )
        ;;
    rest)
        PLAN=(
            "16384 1,2,3,4,5 24:00:00 NB_MIN=256,NB_MAX=384"
            "16384 1,2,3,4,5 24:00:00 NB_MIN=32,NB_MAX=248"
        )
        ;;
    *)
        echo "使い方: bash scripts/flow/submit_16384.sh {high|rest} [--submit]"
        exit 1
        ;;
esac

flow_submit_plan "${SUBMIT}"
