#!/usr/bin/env bash
# 計測1: スレッド数だけを変えて QR を測る（週次まとめ 2026-11-11「追加で行う計測」の1）
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MACHINE=epyc7543
export THREADS="${THREADS:-4 8 16 32}"
export SIZES="${SIZES:-4096 8192}"
export NB_LIST="${NB_LIST:-32 64 96 128 192 256}"
export IB_DIV="${IB_DIV:-4}"
export REPS="${REPS:-5}"
bash "${HERE}/../../../lib/thread_scaling.sh"
