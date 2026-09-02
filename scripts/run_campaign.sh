#!/usr/bin/env bash
# =============================================================================
# run_campaign.sh — 任意のコマンドを tmux セッション内で実行する汎用ラッパー
#
# tmux 内で実行するため SSH が切れても継続する。出力は logs/ に tee される。
# 計測・チューニングともこのラッパー経由で回す（対象は -- 以降のコマンドで指定）。
#
# 使い方:
#   scripts/run_campaign.sh -- python -m plasma_perf bench tileqr --size 4096 --trials 5
#   scripts/run_campaign.sh -- python -m plasma_perf bench ssrfb --exe /path/NoFlush --trials 5
#   SESSION=tune scripts/run_campaign.sh -- python -m plasma_perf tune ssrfb --counts 5 --trials 20
#
# 環境変数:
#   SESSION            tmux セッション名（既定 bench）
#   PLASMA_WORKSPACE   plasma-workspace のパス（config.sh 探索用。既定 ../plasma-workspace）
#
# 進捗確認:
#   tmux attach -t bench   （Ctrl-b d で離脱） / tail -f logs/<最新>.log / tmux ls
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC_DIR="${REPO_ROOT}/src"

SESSION="${SESSION:-bench}"

# -- 以降を実行コマンドとして取り出す
CMD_ARGS=()
seen_sep=0
for arg in "$@"; do
    if [ "${seen_sep}" -eq 0 ] && [ "${arg}" = "--" ]; then
        seen_sep=1
        continue
    fi
    if [ "${seen_sep}" -eq 1 ]; then
        CMD_ARGS+=("${arg}")
    fi
done
# -- が無い場合は全引数をコマンドとして扱う
if [ "${seen_sep}" -eq 0 ]; then
    CMD_ARGS=("$@")
fi

if [ "${#CMD_ARGS[@]}" -eq 0 ]; then
    echo "[ERROR] 実行するコマンドがありません。"
    echo "        例: scripts/run_campaign.sh -- python -m plasma_perf bench tileqr --trials 5"
    exit 1
fi

if ! command -v tmux &>/dev/null; then
    echo "[ERROR] tmux が見つかりません。 sudo apt install -y tmux で導入してください。"
    exit 1
fi

if tmux has-session -t "${SESSION}" 2>/dev/null; then
    echo "[ERROR] tmux セッション '${SESSION}' は既に存在します。"
    echo "        確認: tmux attach -t ${SESSION}"
    echo "        別名で起動: SESSION=bench2 scripts/run_campaign.sh -- <コマンド>"
    exit 1
fi

# plasma-workspace の config.sh を探索（環境変数 PLASMA_TEST / NOFLUSH_PATH を供給）
WORKSPACE_DIR="${PLASMA_WORKSPACE:-${REPO_ROOT}/../plasma-workspace}"
CONFIG=""
if [ -f "${WORKSPACE_DIR}/setup/config.sh" ]; then
    CONFIG="${WORKSPACE_DIR}/setup/config.sh"
fi

HOSTNAME_SHORT="$(hostname -s)"
TS="$(date +%Y%m%d_%H%M%S)"
LOG="${REPO_ROOT}/logs/${HOSTNAME_SHORT}_${SESSION}_${TS}.log"
mkdir -p "${REPO_ROOT}/logs"

# 実行コマンド文字列を安全に組み立てる（%q でエスケープ）
CMDSTR="$(printf '%q ' "${CMD_ARGS[@]}")"

# tmux 内で走らせる bash スクリプト本体を組み立てる。
# ポイント: コマンドが失敗（非ゼロ終了）したらセッションを保持し、エラーを画面に残す。
# （そうしないと即失敗時に tmux が閉じてしまい "no sessions" になって原因が分からない）
SCRIPT="cd $(printf %q "${REPO_ROOT}") || exit 1
export PYTHONPATH=$(printf %q "${SRC_DIR}")\${PYTHONPATH:+:\$PYTHONPATH}"
if [ -n "${CONFIG}" ]; then
    SCRIPT+="
source $(printf %q "${CONFIG}")"
fi
SCRIPT+="
${CMDSTR}2>&1 | tee $(printf %q "${LOG}")
ec=\${PIPESTATUS[0]}
echo
echo \"[run_campaign] 終了コード=\$ec  ログ: ${LOG}\"
if [ \"\$ec\" -ne 0 ]; then
    echo \"[run_campaign] 失敗しました。上の出力を確認してください。\"
    echo \"[run_campaign] このセッションは保持しています（閉じる: exit / tmux kill-session -t ${SESSION}）。\"
    exec bash
fi"

echo "======================================================"
echo " tmux セッション '${SESSION}' でコマンドを開始します"
echo "   host    : ${HOSTNAME_SHORT}"
echo "   command : ${CMD_ARGS[*]}"
echo "   config  : ${CONFIG:-(なし: 環境変数はデフォルト値)}"
echo "   log     : ${LOG}"
echo "======================================================"

tmux new-session -d -s "${SESSION}" "bash -c $(printf %q "${SCRIPT}")"

echo "[起動] tmux セッション '${SESSION}' で実行を開始しました。"
echo ""
echo "  進捗を見る:   tmux attach -t ${SESSION}    （Ctrl-b d で離脱）"
echo "  ログ追尾:     tail -f ${LOG}"
echo "  状態確認:     tmux ls"
