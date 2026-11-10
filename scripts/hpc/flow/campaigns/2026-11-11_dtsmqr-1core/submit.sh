#!/usr/bin/env bash
# =============================================================================
# submit.sh — 不老・弐 Type I で DTSMQR の1コア計測（size 4096、5回）を投入する
#
#   1ジョブ = 1ノードで、nb 32〜512・ib 8〜nb/2（刻み 4、3963 点）を5回順に流す。
#   見積もりは約 1.7 ノード時間（約 200 ポイント）。EPYC 7543 の1回約 20 分からの概算で、
#   不老・弐では測っていない。walltime は 4 時間（実行中は walltime の分が先に計上される）。
#
#   不老・弐は有償で、ゼミで許可された計測だけを流す。投入の前に許可を確かめること。
#
#   使い方（ログインノードで、plasma-bench の直下から）:
#       bash scripts/hpc/flow/campaigns/2026-11-11_dtsmqr-1core/submit.sh            # 投入するコマンドを表示するだけ
#       bash scripts/hpc/flow/campaigns/2026-11-11_dtsmqr-1core/submit.sh --submit   # 実際に qsub する
# =============================================================================
set -euo pipefail

JOB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/job_ssrfb.sh"
cmd=(qsub -N dtsmqr_s4096 -l walltime=04:00:00 -v SIZE=4096,REPS=1:2:3:4:5 "${JOB}")

echo "${cmd[*]}"
if [ "${1:-}" = "--submit" ]; then
    command -v qsub > /dev/null || { echo "[ERROR] qsub が見つかりません（ログインノードで実行すること）"; exit 1; }
    "${cmd[@]}"
else
    echo "（表示だけ。投入するには --submit を付ける）"
fi
