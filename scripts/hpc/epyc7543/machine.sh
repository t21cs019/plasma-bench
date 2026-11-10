#!/usr/bin/env bash
# =============================================================================
# machine.sh — 研究室の EPYC 7543（32 コア、NUMA ノード 1 つ、L3 は 4 コアごとに 8 個）の設定
#
#   scripts/hpc/lib/common.sh が MACHINE=epyc7543 のときに source する。単体では実行しない。
# =============================================================================

# 構成名の前半（common.sh が後ろに _smt-on / _smt-off を付ける）。tileQR_data の epyc_s1_smt-off にそろえる
MACHINE_CONFIG="epyc_s1"
# 既定のスレッド数 = 物理コア数
MACHINE_P=32
