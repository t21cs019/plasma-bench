#!/usr/bin/env bash
# =============================================================================
# run_benchmark.sh — ベンチマーク実行ラッパー
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"

export PYTHONPATH="${SRC_DIR}:${PYTHONPATH:-}"

# ---------------------------------------------------------------------------
# config.sh の探索順（上から順に試す）
#
# [1] plasma-workspace の submodule として使っている場合
#       ~/Workspace/plasma-workspace/
#       └── plasma-bench/        ← このスクリプトの場所
#       └── setup/config.sh      ← [1] が見つかるパス
#
# [2] plasma-bench を単体で使っている場合
#       ~/Workspace/plasma-workspace/   ← setup/ がここにある
#       ~/Workspace/plasma-bench/       ← このスクリプトの場所
#       → 親ディレクトリの ../plasma-workspace/setup/config.sh を参照
#
# [3] どちらも見つからない場合
#       環境変数が未設定のまま続行（PLASMA_TEST はデフォルト値を使用）
#       → 環境変数を手動で設定するか、setup/config.sh を直接 source してください
# ---------------------------------------------------------------------------

CONFIG_SUBMODULE="${SCRIPT_DIR}/../setup/config.sh"
CONFIG_STANDALONE="${SCRIPT_DIR}/../plasma-workspace/setup/config.sh"

if [ -f "${CONFIG_SUBMODULE}" ]; then
    # [1] submodule として使っている場合
    source "${CONFIG_SUBMODULE}"
elif [ -f "${CONFIG_STANDALONE}" ]; then
    # [2] 単体で使っている場合
    source "${CONFIG_STANDALONE}"
else
    # [3] どちらも見つからない場合
    echo "[WARN] config.sh が見つかりません。環境変数が未設定の可能性があります。"
    echo "       手動で setup/config.sh を source してから実行するか、"
    echo "       plasma-workspace と同じ親ディレクトリに plasma-bench を配置してください。"
fi

exec "${SCRIPT_DIR}/.venv/bin/python" -m benchmark.benchmark "$@"