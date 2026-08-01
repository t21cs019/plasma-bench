#!/usr/bin/env bash
# =============================================================================
# sync_results.sh — 計測結果CSVを集約先へ転送する
#
# 3つの転送方式に対応:
#   - rclone : OneDrive 等のリモートへアップロード
#   - scp    : 集約機（WSL2/Windowsデスクトップ等）へ直接コピー
#   - http   : ダッシュボードの受信API（ZimaOS等のコンテナ）へPOST → 自動取込
#
# 使い方:
#   bash scripts/sync_results.sh rclone
#   bash scripts/sync_results.sh scp
#   bash scripts/sync_results.sh http
#
# 転送先は環境変数で上書き:
#   RCLONE_REMOTE   例: "onedrive:research/tileQR/inbox/manual"
#   SCP_DEST        例: "ryo@desktop:~/tileQR_dashboard/inbox/manual"
#   HTTP_URL        例: "http://zima:8502"（受信APIのベースURL）
#   HTTP_KEY        例: "calc"（ダッシュボード sources.toml のソースキー）
#   HTTP_TOKEN      任意。受信APIに DASHBOARD_TOKEN を設定している場合に一致させる
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# plasma-workspace の config.sh があれば読む（転送先の既定値等を供給できる）
WORKSPACE_DIR="${PLASMA_WORKSPACE:-${REPO_ROOT}/../plasma-workspace}"
if [ -f "${WORKSPACE_DIR}/setup/config.sh" ]; then
    source "${WORKSPACE_DIR}/setup/config.sh"
fi

RESULTS_DIR="${REPO_ROOT}/results/benchmark"
HOSTNAME_SHORT="$(hostname -s)"

RCLONE_REMOTE="${RCLONE_REMOTE:-onedrive:research/tileQR/inbox/manual}"
SCP_DEST="${SCP_DEST:-}"
HTTP_URL="${HTTP_URL:-}"
HTTP_KEY="${HTTP_KEY:-}"
HTTP_TOKEN="${HTTP_TOKEN:-}"

MODE="${1:-}"

if [ -z "${MODE}" ]; then
    echo "使い方: bash scripts/sync_results.sh [rclone|scp|http]"
    exit 1
fi

# 転送対象（このホストのCSV）
mapfile -t CSV_FILES < <(find "${RESULTS_DIR}" -name "${HOSTNAME_SHORT}_*.csv" 2>/dev/null)
if [ "${#CSV_FILES[@]}" -eq 0 ]; then
    echo "[WARN] 転送対象CSVが見つかりません: ${RESULTS_DIR}/${HOSTNAME_SHORT}_*.csv"
    exit 0
fi
echo "転送対象: ${#CSV_FILES[@]} 件のCSV (${HOSTNAME_SHORT})"

case "${MODE}" in
    rclone)
        if ! command -v rclone &>/dev/null; then
            echo "[ERROR] rclone が見つかりません。"
            exit 1
        fi
        echo "rclone でアップロード -> ${RCLONE_REMOTE}"
        for f in "${CSV_FILES[@]}"; do
            rclone copy "${f}" "${RCLONE_REMOTE}/" --progress
        done
        ;;
    scp)
        if [ -z "${SCP_DEST}" ]; then
            echo "[ERROR] SCP_DEST が未設定です。"
            echo "        例: SCP_DEST='ryo@desktop:~/tileQR_dashboard/inbox/manual' bash scripts/sync_results.sh scp"
            exit 1
        fi
        echo "scp で転送 -> ${SCP_DEST}"
        scp "${CSV_FILES[@]}" "${SCP_DEST}/"
        ;;
    http)
        if ! command -v curl &>/dev/null; then
            echo "[ERROR] curl が見つかりません。"
            exit 1
        fi
        if [ -z "${HTTP_URL}" ] || [ -z "${HTTP_KEY}" ]; then
            echo "[ERROR] HTTP_URL と HTTP_KEY が必要です。"
            echo "        例: HTTP_URL=http://zima:8502 HTTP_KEY=calc bash scripts/sync_results.sh http"
            exit 1
        fi
        # 全CSV（＋隣にあれば .meta.json）を1回の multipart POST でまとめて送る。
        # 受信側は着地後に store を1回だけ再構築する。
        FORM_ARGS=()
        for f in "${CSV_FILES[@]}"; do
            FORM_ARGS+=(-F "files=@${f}")
            meta="${f%.csv}.meta.json"
            [ -f "${meta}" ] && FORM_ARGS+=(-F "files=@${meta}")
        done
        HDR_ARGS=()
        [ -n "${HTTP_TOKEN}" ] && HDR_ARGS+=(-H "X-Auth-Token: ${HTTP_TOKEN}")
        ENDPOINT="${HTTP_URL%/}/upload/${HTTP_KEY}"
        echo "HTTP POST -> ${ENDPOINT}（${#CSV_FILES[@]} 件のCSV）"
        curl -fsS "${HDR_ARGS[@]}" "${FORM_ARGS[@]}" "${ENDPOINT}"
        echo
        ;;
    *)
        echo "[ERROR] 未知のモード: ${MODE}（rclone / scp / http を指定）"
        exit 1
        ;;
esac

echo "[完了] 転送が完了しました。"
