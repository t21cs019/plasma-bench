#!/bin/bash
#PBS -q lx
#PBS -b 1
#PBS -l elapstim_req=2:00:00
#PBS -l cpunum_job=128
#PBS -N bind_ab_aoba
#PBS -jo
# -----------------------------------------------------------------------------
# AOBA-B (EPYC 7702 x 2, NPS4): the same OpenMP thread-binding A/B test as on
# Flow-II Type I (see bind_ab.sh). P=64 = socket 0 = NUMA nodes 0-3, which is
# the condition of the past aoba-b_s1_smt-off data.
# ASCII only, as in job_bind_ab.sh.
#
#   qsub scripts/flow/job_bind_ab_aoba.sh
#   qsub -v REPO=$HOME/Workspace/plasma-bench-flow scripts/flow/job_bind_ab_aoba.sh
# -----------------------------------------------------------------------------
REPO="${REPO:-$HOME/Workspace/plasma-bench}"
hostname; date
P=64 bash "${REPO}/scripts/flow/bind_ab.sh"
