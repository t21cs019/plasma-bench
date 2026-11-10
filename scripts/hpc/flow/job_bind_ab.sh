#!/bin/bash
#PBS -q t1-debug
#PBS -l select=1
#PBS -l walltime=01:00:00
#PBS -j oe
#PBS -N bind_ab
# -----------------------------------------------------------------------------
# Flow-II Type I: OpenMP thread-binding A/B test (see bind_ab.sh).
# ASCII only: the PBS job system does not accept multibyte characters.
#
#   qsub scripts/hpc/flow/job_bind_ab.sh
#   qsub -v P=96,SIZES=4096,REPS=3 scripts/hpc/flow/job_bind_ab.sh
# -----------------------------------------------------------------------------
REPO="$HOME/Workspace/plasma-bench"
MACHINE=flow bash "${REPO}/scripts/hpc/lib/bind_ab.sh"
