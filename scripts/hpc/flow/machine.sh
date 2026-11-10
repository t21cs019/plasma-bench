#!/usr/bin/env bash
# =============================================================================
# machine.sh — 名古屋大 不老・弐 Type I（EPYC 9965 × 2、NPS4 想定）の設定
#
#   scripts/hpc/lib/common.sh が MACHINE=flow のときに source する。単体では実行しない。
# =============================================================================

# 構成名の前半（common.sh が後ろに _smt-on / _smt-off を付ける）
MACHINE_CONFIG="flow2-t1_s1"
# 既定のスレッド数 = 1ソケットの物理コア数
MACHINE_P=192
