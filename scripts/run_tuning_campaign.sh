#!/usr/bin/env bash
# =============================================================================
# run_tuning_campaign.sh — tuning/run_tuning.py を tmux セッション内で逐次実行する
#
# scripts/run_campaign.sh（既存の tmux+ログ汎用ラッパー）の上に薄く被せている
# だけ（指示書 3.7「既存の run_campaign.sh と同じ作法で書く」）。tmux 起動・
# 既存セッション名の衝突チェック・ログ tee・失敗時のセッション保持は
# run_campaign.sh 側の実装をそのまま使う（車輪の再発明をしない）。
# ログファイル名は run_campaign.sh の命名規則 logs/<host>_<SESSION>_<ts>.log
# に従う（SESSION 既定値に size を含めるため、実質的に指示書の
# tuning_<host>_size<S>_<ts>.log と同等の情報を持つ）。
#
# 使い方:
#   SIZE=4096 REPEAT=10 THREADS=4 bash scripts/run_tuning_campaign.sh
#   SIZES="1024 2048 4096" REPEAT=10 THREADS=4 bash scripts/run_tuning_campaign.sh
#
# 環境変数:
#   SIZE       単一サイズ（既定 4096）。SIZES 指定時はそちらが優先。
#   SIZES      複数サイズを空白区切りで逐次実行（例 "1024 2048 4096"）
#   REPEAT     各手法の繰り返し回数（既定 5。run_tuning.py --repeat にそのまま渡す）
#   THREADS    OMP_NUM_THREADS（既定 4）
#   METHODS    実行する手法（既定 agullo,optuna_full,model）
#   COOLDOWN   手法間のクールダウン秒数（既定 30。run_tuning.py --cooldown）
#   SESSION    tmux セッション名（既定 tuning）
#   EXTRA_ARGS run_tuning.py にそのまま追加で渡す引数（例 "--nb-max 256"）
#
# 進捗確認:
#   tmux attach -t tuning   （Ctrl-b d で離脱） / tail -f logs/<最新>.log
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SIZE="${SIZE:-4096}"
SIZES="${SIZES:-${SIZE}}"
REPEAT="${REPEAT:-5}"
THREADS="${THREADS:-4}"
METHODS="${METHODS:-agullo,optuna_full,model}"
COOLDOWN="${COOLDOWN:-30}"
SESSION="${SESSION:-tuning}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

# SIZES を逐次実行する1つの bash コマンド文字列を組み立てる
# （run_campaign.sh は単一コマンドを -- 以降で受け取るため、複数サイズは
# ループとして1つの `bash -c` にまとめて渡す）。
RUN_CMD='set -e
export PATH="$HOME/.local/bin:$PATH"'
for s in ${SIZES}; do
    RUN_CMD+=$'\n'"echo '=== size=${s} ==='"
    RUN_CMD+=$'\n'"uv run python tuning/run_tuning.py --size ${s} --methods $(printf %q "${METHODS}") --repeat ${REPEAT} --threads ${THREADS} --cooldown ${COOLDOWN} --output results/tuning/ ${EXTRA_ARGS}"
done

echo "======================================================"
echo " チューニング比較キャンペーンを開始します"
echo "   sizes   : ${SIZES}"
echo "   methods : ${METHODS}"
echo "   repeat  : ${REPEAT}, threads=${THREADS}, cooldown=${COOLDOWN}"
echo "======================================================"

SESSION="${SESSION}" bash "${REPO_ROOT}/scripts/run_campaign.sh" -- bash -c "${RUN_CMD}"
