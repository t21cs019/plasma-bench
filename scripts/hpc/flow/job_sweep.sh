#!/bin/bash
#PBS -q t1-single
#PBS -l select=1
#PBS -l walltime=12:00:00
#PBS -j oe
#PBS -N tileqr
# -----------------------------------------------------------------------------
# Flow-II Type I: tileQR nb/ib sweep, 1 condition x 1 repetition (see tileqr_sweep.sh).
# ASCII only: the PBS job system does not accept multibyte characters.
#
# Production plans are submitted from campaigns/<date>_<name>/.
# Pass the condition with -v, and override walltime per size with -l:
#   qsub -v SIZE=1024:2048,REP=1 scripts/hpc/flow/job_sweep.sh   (sizes run in order)
#   qsub -v SIZE=4096,REP=1 scripts/hpc/flow/job_sweep.sh
#   qsub -v SIZE=8192,REP=2 -l walltime=06:00:00 scripts/hpc/flow/job_sweep.sh
#   qsub -v P=48,SIZE=2048,REP=1 -q t1-share -l select=1:nnumas=1 scripts/hpc/flow/job_sweep.sh
# Variables: P (192) SIZE (required) REP (1) BIND (close|none) MEM (bind|interleave)
#            STEP NB_MIN NB_MAX
# -----------------------------------------------------------------------------
REPO="$HOME/Workspace/plasma-bench"
MACHINE=flow bash "${REPO}/scripts/hpc/lib/tileqr_sweep.sh"
