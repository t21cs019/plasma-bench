#!/usr/bin/env bash
# =============================================================================
# sync_plan.sh — 計測プラン(plan/PROGRESS.md)を集約ダッシュボードと往復同期する
#
# 計測の自動化機能（plan）の PROGRESS.md を、ダッシュボード（ZimaOS上のコンテナ）
# と HTTP でやり取りする。ダッシュボード側で行追加・note/skip 編集した内容を
# この計測機に取り戻し、`plan run` に反映するのが狙い。
#
#   push : ローカルの plan/PROGRESS.md をダッシュボードへアップロード（編集の起点）
#   pull : ダッシュボードで編集された PROGRESS.md を取得してローカルへ上書き
#
# 設定（環境変数。plasma-workspace の config.sh でも供給可）:
#   HTTP_URL    受信APIのベースURL       例: http://zima:8502
#   HTTP_KEY    ソースキー(sources.toml)  例: calc
#   HTTP_TOKEN  共有トークン（任意。ダッシュボードの DASHBOARD_TOKEN と一致させる）
#
# 使い方:
#   HTTP_URL=http://zima:8502 HTTP_KEY=calc bash scripts/sync_plan.sh push
#   HTTP_URL=http://zima:8502 HTTP_KEY=calc bash scripts/sync_plan.sh pull
#
# 典型フロー:
#   1. python3 -m plasma_perf plan scan          # 進捗を最新化
#   2. bash scripts/sync_plan.sh push            # ダッシュボードへ送って編集してもらう
#   3. （ダッシュボードで行追加や skip を編集・保存）
#   4. bash scripts/sync_plan.sh pull            # 編集版を取り戻す
#   5. python3 -m plasma_perf plan scan          # status/coverage を再計算
#   6. python3 -m plasma_perf plan run           # 未計測分を実行
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# plasma-workspace の config.sh があれば読む（HTTP_URL 等の既定値を供給できる）
WORKSPACE_DIR="${PLASMA_WORKSPACE:-${REPO_ROOT}/../plasma-workspace}"
if [ -f "${WORKSPACE_DIR}/setup/config.sh" ]; then
    # shellcheck disable=SC1091
    source "${WORKSPACE_DIR}/setup/config.sh"
fi

PROGRESS_MD="${REPO_ROOT}/plan/PROGRESS.md"
HTTP_URL="${HTTP_URL:-}"
HTTP_KEY="${HTTP_KEY:-}"
HTTP_TOKEN="${HTTP_TOKEN:-}"

MODE="${1:-}"

if ! command -v curl &>/dev/null; then
    echo "[ERROR] curl が見つかりません。"
    exit 1
fi
if [ -z "${HTTP_URL}" ] || [ -z "${HTTP_KEY}" ]; then
    echo "[ERROR] HTTP_URL と HTTP_KEY が必要です。"
    echo "        例: HTTP_URL=http://zima:8502 HTTP_KEY=calc bash scripts/sync_plan.sh push"
    exit 1
fi

HDR_ARGS=()
[ -n "${HTTP_TOKEN}" ] && HDR_ARGS+=(-H "X-Auth-Token: ${HTTP_TOKEN}")
ENDPOINT="${HTTP_URL%/}/plan/${HTTP_KEY}"

case "${MODE}" in
    push)
        if [ ! -f "${PROGRESS_MD}" ]; then
            echo "[ERROR] ${PROGRESS_MD} がありません。先に plan scan / plan add を実行してください。"
            exit 1
        fi
        echo "PROGRESS.md を送信 -> ${ENDPOINT}"
        curl -fsS "${HDR_ARGS[@]}" \
            -H "Content-Type: text/markdown; charset=utf-8" \
            --data-binary @"${PROGRESS_MD}" \
            "${ENDPOINT}"
        echo
        echo "[完了] ダッシュボードで編集後、pull で取り戻せます。"
        ;;
    pull)
        mkdir -p "$(dirname "${PROGRESS_MD}")"
        if [ -f "${PROGRESS_MD}" ]; then
            cp "${PROGRESS_MD}" "${PROGRESS_MD}.bak"
            echo "既存を退避: ${PROGRESS_MD}.bak"
        fi
        echo "PROGRESS.md を取得 <- ${ENDPOINT}"
        curl -fsS "${HDR_ARGS[@]}" "${ENDPOINT}" -o "${PROGRESS_MD}"
        echo "[完了] ${PROGRESS_MD} を更新しました。"
        echo "        反映: python3 -m plasma_perf plan scan && python3 -m plasma_perf plan run"
        ;;
    *)
        echo "使い方: bash scripts/sync_plan.sh [push|pull]"
        exit 1
        ;;
esac
