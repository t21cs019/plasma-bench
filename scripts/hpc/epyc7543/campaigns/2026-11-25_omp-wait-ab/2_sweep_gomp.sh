#!/usr/bin/env bash
# =============================================================================
# 2_sweep_gomp.sh — EPYC 7543 で size 1024 の nb・ib スイープを libgomp（gompfwd.so）で取る（2026-11-25）
#
#   比べる相手：tileQR_data の raw/qr_sweep/epyc_s1_smt-off/epyc_size1024_t32_nb32-512_20261027-080944_r{1..5}.csv
#   （libiomp5。plasma_perf bench tileqr --size 1024 --trials 5 で取ったもの）。格子と流し方はそれと同じにする：
#     plasma_perf bench tileqr --size 1024 --threads 32 --nb-min 32 --nb-max 512 --step 4（ib は 8〜nb/2、4 刻み）--trials 5
#   違うのは OpenMP のランタイムだけ：
#     LD_PRELOAD=gompfwd.so（--wrapper で plasmatest にだけ付ける）、OMP_PLACES=cores OMP_PROC_BIND=close（rt.sh の gdef）
#   common.sh の hpc_set_cond は plasmatest の ldd に libiomp5 があると KMP_AFFINITY を選び、libgomp では固定が効かない。
#   ここでは hpc_set_cond を使わず、rt.sh の rt_set gdef で固定を決める。
#
#   つなぎ（bridge）：スイープの前と後に、idef（libiomp5、KMP_AFFINITY）と gdef を、size 1024 の
#   nb 32・64・96・128・192（ib は best_ib_epyc7543_p32.txt）で 3 回ずつ交互に流す（各 30 回、1 分かからない）。
#   10-27 の値と今日の libiomp5 の差（日による違い）と、スイープの間に機械の状態が変わらなかったかを見る。
#
#   使い方（plasma-bench の直下で。ブランチ feat/omp-wait-ab。S=scripts/hpc/epyc7543/campaigns/2026-11-25_omp-wait-ab。
#           0_preflight.sh の 14 が通ってから。1_run.sh の後に、tmux の中で）:
#     MACHINE=epyc7543 bash $S/2_sweep_gomp.sh        （見込み約 2 時間）
#   変数: TRIALS（既定 5） NB_MIN・NB_MAX（既定 32・512） STEP（既定 4） BRIDGE_REPS（既定 3） MAX_OTHER_CPU（既定 100） FORCE
#   結果: results/benchmark/<config>_gomp-sweep1024_<時刻>/
#         sweep.csv（threads,size,nb,ib,GFlops。trial 1〜5 が順に並ぶ）、bridge.csv、other_cpu.csv、meta.txt、affinity_gdef_p32.txt
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACHINE=${MACHINE:?MACHINE=epyc7543 を付ける}
export MACHINE
PB="${PLASMA_BENCH:-$PWD}"
# shellcheck source=/dev/null
source "${PB}/scripts/hpc/lib/common.sh"
# shellcheck source=/dev/null
source "${HERE}/rt.sh"

SIZE=1024
P=32
TRIALS=${TRIALS:-5}
NB_MIN=${NB_MIN:-32}
NB_MAX=${NB_MAX:-512}
STEP=${STEP:-4}
BRIDGE_REPS=${BRIDGE_REPS:-3}
BRIDGE_NB="32 64 96 128 192"
IB_TABLE=${HERE}/best_ib_epyc7543_p32.txt
MAX_OTHER_CPU=${MAX_OTHER_CPU:-100}
FORCE=${FORCE:-0}

hpc_setup
hpc_select_nodes "${P}"
GOMPFWD="${HERE}/gompfwd/gompfwd.so"
[ -f "${GOMPFWD}" ] || bash "${HERE}/mk_gompfwd.sh" "${PLASMA_TEST}" "${HERE}/gompfwd"
if [ -s "${HERE}/gompfwd/gompfwd_missing.txt" ] && [ "${FORCE}" != 1 ]; then
    echo "[ERROR] libgomp に無い OpenMP の名前がある（${HERE}/gompfwd/gompfwd_missing.txt）。FORCE=1 で無視"
    exit 1
fi
other_cpu() { ps -eo user:20,pcpu | awk -v me="$(id -un)" 'NR>1 && $1!=me {s+=$2} END{printf "%.0f", s+0}'; }
oc=$(other_cpu)
if [ "${oc}" -gt "${MAX_OTHER_CPU}" ] && [ "${FORCE}" != 1 ]; then
    echo "[ERROR] 他のユーザーの CPU 使用率の合計が ${oc}%（上限 ${MAX_OTHER_CPU}%）。共用機なので中止。FORCE=1 で無視できる"
    ps -eo user:20,pcpu,etime,args --sort=-pcpu | head -6
    exit 1
fi
ib_of() { awk -v s="$1" -v n="$2" '!/^#/ && $1 == s && $2 == n {print $3; exit}' "${IB_TABLE}"; }

OUTDIR="results/benchmark/${CONFIG}_gomp-sweep1024_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${OUTDIR}"

# 他のユーザーの CPU 使用率を 60 秒ごとに残す（スイープの間）
( echo "time,other_cpu,load1"; while :; do echo "$(date +%F_%T),$(other_cpu),$(cut -d' ' -f1 /proc/loadavg)"; sleep 60; done ) > "${OUTDIR}/other_cpu.csv" &
MON=$!
trap 'kill ${MON} 2>/dev/null || true' EXIT

hpc_meta > "${OUTDIR}/meta.txt"
LIBGOMP=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libgomp\.so/ {print $3; exit}')")
LIBIOMP=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libiomp5/ {print $3; exit}')")
LIB=$(readlink -f "$(ldd "${PLASMA_TEST}" | awk '/libopenblas/ {print $3}')")
{
    echo
    echo "# === 計測条件 ==="
    echo "SIZE=${SIZE} P=${P} TRIALS=${TRIALS} NB=${NB_MIN}-${NB_MAX} STEP=${STEP}（ib は 8〜nb/2、plasma_perf の既定） BRIDGE_REPS=${BRIDGE_REPS} BRIDGE_NB=${BRIDGE_NB}"
    echo "libopenblas: ${LIB}  md5 $(md5sum "${LIB}" | cut -d' ' -f1)"
    python3 -c "import ctypes,sys; l=ctypes.CDLL(sys.argv[1]); l.openblas_get_config.restype=ctypes.c_char_p; print('openblas_get_config:', l.openblas_get_config().decode())" "${LIB}"
    echo "libiomp5: ${LIBIOMP}  md5 $(md5sum "${LIBIOMP}" | cut -d' ' -f1)"
    echo "libgomp: ${LIBGOMP}  md5 $(md5sum "${LIBGOMP}" | cut -d' ' -f1)  $(dpkg-query -W -f='${Package} ${Version}' libgomp1 2>/dev/null)"
    echo "gompfwd: ${GOMPFWD}  md5 $(md5sum "${GOMPFWD}" | cut -d' ' -f1)"
    for rt in idef gdef; do rt_set "${rt}"; echo "${rt}: $(rt_env_line)"; done
    echo "plasma-bench: $(git -C "${PB}" rev-parse --abbrev-ref HEAD) $(git -C "${PB}" rev-parse --short HEAD)"
} >> "${OUTDIR}/meta.txt"
hpc_set_cond close bind      # NUMACTL_ARGS を作るためだけ（固定の変数は rt_set が上書きする）
rt_proc_check gdef "${P}" "${OUTDIR}/affinity_gdef_p${P}.txt"
echo "affinity gdef p${P}: $(grep -E '^threads=|^distinct_cpus=' "${OUTDIR}/affinity_gdef_p${P}.txt" | tr '\n' ' ')" | tee -a "${OUTDIR}/meta.txt"
hpc_snapshot "開始" | tee -a "${OUTDIR}/meta.txt"

# つなぎ。$1=before|after。スイープと同じく numactl なしで、plasmatest を直接流す
bridge() {
    local phase=$1 rep nb ib rt j t gf ex
    [ -f "${OUTDIR}/bridge.csv" ] || echo "phase,rep,rt,size,nb,ib,Time_sec,GFlops,other_cpu_before,exit" > "${OUTDIR}/bridge.csv"
    local -a rts=(idef gdef)
    for rep in $(seq 1 "${BRIDGE_REPS}"); do
        for nb in ${BRIDGE_NB}; do
            ib=$(ib_of "${SIZE}" "${nb}")
            for j in 0 1; do
                rt=${rts[$(( (j + rep) % 2 ))]}
                rt_set "${rt}"
                ex=0
                out=$(OMP_NUM_THREADS="${P}" env LD_PRELOAD="$(rt_preload "")" "${PLASMA_TEST}" dgeqrf --dim="${SIZE}x${SIZE}" --nb="${nb}" --ib="${ib}" --test=n 2>&1) || ex=$?
                read -r t gf < <(echo "${out}" | awk '$3 ~ /^[0-9]+\.[0-9]+$/ && $4 ~ /^[0-9]+\.[0-9]+$/ {print $3, $4; exit}' || true)
                echo "${phase},${rep},${rt},${SIZE},${nb},${ib},${t:-},${gf:-},$(other_cpu),${ex}" >> "${OUTDIR}/bridge.csv"
                printf "bridge %-6s r%d %-4s nb %3d ib %3d  %8s GFlops\n" "${phase}" "${rep}" "${rt}" "${nb}" "${ib}" "${gf:--}"
            done
        done
    done
}

echo "===== bridge（前） ====="
bridge before
echo "===== sweep start（gdef、size ${SIZE}、trials ${TRIALS}） $(date '+%F %T') ====="
SECONDS=0
rt_set gdef
OMP_NUM_THREADS="${P}" "${PY}" -m plasma_perf bench tileqr \
    --size "${SIZE}" --threads "${P}" \
    --nb-min "${NB_MIN}" --nb-max "${NB_MAX}" --step "${STEP}" \
    --trials "${TRIALS}" \
    --wrapper "env LD_PRELOAD=${GOMPFWD}" \
    --output "${OUTDIR}/sweep.csv" \
    2>&1 | tee "${OUTDIR}/sweep_console.txt" || echo "[warn] plasma_perf が 0 以外で終わった" | tee -a "${OUTDIR}/meta.txt"
echo "===== sweep end（${SECONDS} sec） $(date '+%F %T') =====" | tee -a "${OUTDIR}/meta.txt"
echo "===== bridge（後） ====="
bridge after
hpc_snapshot "終了" | tee -a "${OUTDIR}/meta.txt"
echo "rows: $(( $(wc -l < "${OUTDIR}/sweep.csv") - 1 ))（期待 ${TRIALS} × 1 trial の点の数。sweep_console.txt の points/trial を見る）"
echo "結果: ${OUTDIR}（ディレクトリごと devbox-lab の ~/Download/epyc/ に持ち帰る）"
