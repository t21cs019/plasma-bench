#!/usr/bin/env bash
# =============================================================================
# setup_python.sh — uv で Python 環境（チューニング/可視化用）を構築する
#
# 計測（bench）だけなら不要（標準ライブラリで動く）。optuna を使うチューニングや
# ヒートマップ可視化を行うときにこれを実行する。uv.lock でバージョンを固定・再現する。
#
# 使い方:
#   bash scripts/setup_python.sh              # tuning + viz を導入（既定）
#   bash scripts/setup_python.sh --extra tuning   # tuning のみ
#   bash scripts/setup_python.sh --all-extras     # 全 extra
#
# 実行後の使い方:
#   uv run python -m plasma_perf tune ssrfb --counts 5 --trials 20
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# --- 1. uv のインストール（管理者権限不要）-----------------------------------
if ! command -v uv &>/dev/null; then
    echo "[1/2] uv をインストールしています..."
    if command -v curl &>/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget &>/dev/null; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        echo "[ERROR] curl も wget も見つかりません。どちらかを用意してください。"
        exit 1
    fi
    export PATH="${HOME}/.local/bin:${PATH}"
    # uv の PATH を ~/.bashrc に永続化
    UV_PATH_LINE='export PATH="${HOME}/.local/bin:${PATH}"'
    if ! grep -qF "${UV_PATH_LINE}" "${HOME}/.bashrc" 2>/dev/null; then
        echo "${UV_PATH_LINE}" >> "${HOME}/.bashrc"
    fi
else
    echo "[1/2] uv はインストール済みです: $(uv --version)"
fi

# --- 2. uv sync で依存を導入（uv.lock に固定）--------------------------------
echo "[2/2] uv sync で依存パッケージを導入しています..."
if [ "$#" -gt 0 ]; then
    uv sync "$@"
else
    uv sync --extra tuning --extra viz
fi

echo ""
echo "[完了] Python 環境の構築が完了しました（.venv/）。"
echo "       実行例: uv run python -m plasma_perf tune ssrfb --counts 5 --trials 20"
