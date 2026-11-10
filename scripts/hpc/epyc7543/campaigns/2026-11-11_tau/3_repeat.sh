#!/usr/bin/env bash
# 計測3: 同じプロセスで複数回分解する（週次まとめ 2026-11-11「追加で行う計測」の3）。
# 不老・弐で流す前の確認。EPYC 7543 では固定の差がほぼないので、1回目と2回目以降の差を見る
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MACHINE=epyc7543
export P="${P:-32}"
export SIZES="${SIZES:-2048 4096}"
export NB_LIST="${NB_LIST:-192 256}"
export REPEAT="${REPEAT:-3}"
export PROCS="${PROCS:-5}"
bash "${HERE}/../../../lib/repeat_ab.sh"
