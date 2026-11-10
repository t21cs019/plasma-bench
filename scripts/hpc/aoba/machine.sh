#!/usr/bin/env bash
# =============================================================================
# machine.sh — 東北大 AOBA-B（EPYC 7702 × 2、NPS4）の設定
#
#   scripts/hpc/lib/common.sh が MACHINE=aoba のときに source する。単体では実行しない。
# =============================================================================

# 構成名の前半（common.sh が後ろに _smt-on / _smt-off を付ける）。これまでの aoba-b_s1_smt-off にそろえる
MACHINE_CONFIG="aoba-b_s1"
# 既定のスレッド数 = 1ソケットの物理コア数（NUMA ノード 0-3）
MACHINE_P=64
