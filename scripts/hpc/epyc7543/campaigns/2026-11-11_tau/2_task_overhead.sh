#!/usr/bin/env bash
# 計測2: 空のタスクで τ を測る。libgomp と libiomp5 の両方（週次まとめ 2026-11-11「追加で行う計測」の2）
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MACHINE=epyc7543
export THREADS="${THREADS:-1 2 4 8 16 32}"
export TILES="${TILES:-64 128 256}"     # size 8192 の nb 128 / 64 / 32 に相当
export WORK_NS="${WORK_NS:-0 1000}"
export REPS="${REPS:-5}"
export ITERS="${ITERS:-3}"
bash "${HERE}/../../../lib/task_overhead.sh"
