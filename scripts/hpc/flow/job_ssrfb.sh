#!/bin/bash
#PBS -q t1-single
#PBS -l select=1
#PBS -l walltime=04:00:00
#PBS -j oe
#PBS -N dtsmqr
# -----------------------------------------------------------------------------
# Flow-II Type I: DTSMQR kernel alone (NoFlush) on 1 pinned core, full nb/ib grid.
# ASCII only: the PBS job system does not accept multibyte characters.
#
# Production plans are submitted from campaigns/<date>_<name>/.
#   qsub -v SIZE=4096,REPS=1:2:3:4:5 scripts/hpc/flow/job_ssrfb.sh
# Variables: SIZE (4096) REPS (1:2:3:4:5) CORE (1) STEP (4) NB_MIN NB_MAX N_IT (50)
# See scripts/hpc/lib/ssrfb_sweep.sh.
# -----------------------------------------------------------------------------
REPO="$HOME/Workspace/plasma-bench"
MACHINE=flow bash "${REPO}/scripts/hpc/lib/ssrfb_sweep.sh"
