#!/usr/bin/env bash
# =============================================================================
# sync_results.sh — 計測結果CSVを集約先へ転送する
#
# 2つの転送方式に対応:
#   - rclone : OneDrive 等のリモートへアップロード
#   - scp    : 集約機（WSL2/Windowsデスクトップ等）へ直接コピー
#
# 使い方:
#   bash scripts/sync_results.sh rclone
#   bash scripts/sync_results.sh scp
#
# 転送先は環境変数で上書き:
#   RCLONE_REMOTE   例: "onedrive:research/tileQR/inbox/manual"
#   SCP_DEST        例: "ryo@desktop:~/tileQR_dashboard/inbox/manual"
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

MODE="${1:-}"

if [ -z "${MODE}" ]; then
    echo "使い方: bash scripts/sync_results.sh [rclone|scp]"
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
    *)
        echo "[ERROR] 未知のモード: ${MODE}（rclone か scp を指定）"
        exit 1
        ;;
esac

echo "[完了] 転送が完了しました。"
