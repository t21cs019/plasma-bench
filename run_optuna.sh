#!/usr/bin/env bash
# Optunaチューニング実行ラッパー
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"

export PYTHONPATH="${SRC_DIR}:${PYTHONPATH:-}"

# config.sh が存在すれば読み込む（PLASMA_TEST・NOFLUSH_PATH等の環境変数を設定）
CONFIG="${SCRIPT_DIR}/../plasma-workspace/setup/config.sh"
if [ -f "${CONFIG}" ]; then
    source "${CONFIG}"
fi

exec "${SCRIPT_DIR}/.venv/bin/python" -m tuning.main "$@"
