#!/usr/bin/env bash
# =============================================================================
# common.sh — 不老・弐 Type I（EPYC 9965 × 2、NPS4 想定）の tileQR 計測で共通に使う関数
#
#   bind_ab.sh / tileqr_sweep.sh から source して使う。単体では実行しない。
#
#   AOBA-B（gen_tileQR_jobs.sh）からの変更点:
#     * OpenMP のスレッドを固定できるようにした（OMP_PLACES=cores, OMP_PROC_BIND=close）。
#       これまでの計測は numactl でソケットを決めただけで、スレッドはその中を動いていた。
#     * 固定が効いたかを、実行前に OMP_DISPLAY_AFFINITY で検査する。
#     * NUMA ノードは「ソケット 0 のノードを番号順に、P コアをまかなえるだけ」取る。
#     * 掴んだコア数は論理 CPU ではなく物理コアで数える（SMT が有効でも p = 物理コア数）。
# =============================================================================

FLOW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${FLOW_DIR}/../.." && pwd)"
WSPACE="${PLASMA_WORKSPACE:-${REPO}/../plasma-workspace}"

# --- 環境の準備と事前確認 ----------------------------------------------------
flow_setup() {
    cd "${REPO}" || { echo "[ERROR] repo not found: ${REPO}"; exit 1; }
    export PYTHONPATH=src
    # shellcheck source=/dev/null
    source "${WSPACE}/setup/config.sh"
    # OpenBLAS は USE_OPENMP=1 なので PLASMA のタスク内では元から1スレッドだが、念のため固定する
    export OPENBLAS_NUM_THREADS=1
    mkdir -p logs results/benchmark

    PY=python3
    [ -x .venv/bin/python ] && PY=./.venv/bin/python

    [ -x "${PLASMA_TEST}" ] || { echo "[ERROR] not executable: ${PLASMA_TEST}"; exit 1; }
    if ldd "${PLASMA_TEST}" | grep -q 'not found'; then
        echo "[ERROR] unresolved libs"; ldd "${PLASMA_TEST}"; exit 1
    fi
    command -v numactl > /dev/null || { echo "[ERROR] numactl not found"; exit 1; }

    SMT=$(cat /sys/devices/system/cpu/smt/active 2>/dev/null || echo "?")
    case "${SMT}" in
        0) SMT_TAG=smt-off ;;
        1) SMT_TAG=smt-on ;;
        *) SMT_TAG=smt-unknown ;;
    esac
    CONFIG="flow2-t1_s1_${SMT_TAG}"
}

# --- 使う NUMA ノードを決める -------------------------------------------------
# ソケット 0 のノードを番号順に並べ、P コアをまかなえる数だけ先頭から取る。
#   P=16 → ノード 0（48コアのうち先頭16コア = 1 CCD を close で使う）
#   P=48 → 0、P=96 → 0,1、P=192 → 0-3（NPS4・ノード専有の場合）
# t1-share はジョブに NUMA 単位でコアを割り当てるので、どのノードになるかは決まっていない。
# そのため「ジョブに割り当てられたコアをすべて含むノード」だけを候補にし、
# 候補が最も多いソケットから選ぶ（同数なら番号の小さいソケット）。
# 結果は NODES（例 "0,1,2,3"）、CORES_PER_NODE に入る。
flow_select_nodes() {
    local p=$1
    local -a usable
    # "ソケット ノード" の組。割り当てられた CPU だけでできているノードに限る
    local pairs
    pairs=$(numactl --show | awk '/^physcpubind:/{for (i = 2; i <= NF; i++) print $i}' \
        | awk -F, 'NR == FNR {ok[$1] = 1; next}
                   /^#/ {next}
                   {tot[$4]++; sock[$4] = $3; if ($1 in ok) got[$4]++}
                   END {for (n in tot) if (got[n] == tot[n]) print sock[n], n}' \
            - <(lscpu -p=CPU,CORE,SOCKET,NODE) \
        | sort -k1,1n -k2,2n)
    [ -n "${pairs}" ] || { echo "[ERROR] 使える NUMA ノードが見つかりません"; exit 1; }

    local sock
    sock=$(echo "${pairs}" | awk '{c[$1]++} END {for (s in c) print c[s], s}' \
        | sort -k1,1nr -k2,2n | head -n 1 | awk '{print $2}')
    read -r -a usable <<< "$(echo "${pairs}" | awk -v s="${sock}" '$1 == s {print $2}' | tr '\n' ' ')"

    CORES_PER_NODE=$(lscpu -p=CPU,CORE,SOCKET,NODE \
        | awk -F, -v n="${usable[0]}" '!/^#/ && $4 == n {print $2}' | sort -u | wc -l)
    local need=$(( (p + CORES_PER_NODE - 1) / CORES_PER_NODE ))
    if [ "${need}" -gt "${#usable[@]}" ]; then
        echo "[ERROR] P=${p} はソケット${sock}の使えるノード（${usable[*]}、各 ${CORES_PER_NODE} コア）に収まりません"
        echo "        割り当て: $(echo "${pairs}" | awk '{printf "socket%s:node%s ", $1, $2}')"
        exit 1
    fi
    NODES=$(IFS=,; echo "${usable[*]:0:need}")
    SOCKET=${sock}
}

# --- 条件を設定する -----------------------------------------------------------
# $1 = スレッド固定: close | none
# $2 = メモリの置き方: bind（--membind）| interleave（--interleave）
# NUMACTL_ARGS と、OMP_PLACES / OMP_PROC_BIND の環境変数を設定する。
# plasmatest が Intel の OpenMP（libiomp5）を直接リンクしている場合は、OMP_PLACES=cores で
# 全スレッドが CPU 0 に固定されてしまう（研究室の EPYC 7543、2026-11-01）。そのときは
# Intel の KMP_AFFINITY で同じ並べ方（1スレッド1コア、先頭から詰める）を指定する。
flow_set_cond() {
    local bind=$1 mem=$2
    unset OMP_PLACES OMP_PROC_BIND KMP_AFFINITY
    case "${bind}" in
        close)
            if ldd "${PLASMA_TEST}" | grep -q libiomp5; then
                export KMP_AFFINITY=granularity=core,compact
            else
                export OMP_PLACES=cores OMP_PROC_BIND=close
            fi
            ;;
        none) ;;
        *) echo "[ERROR] unknown bind: ${bind}"; exit 1 ;;
    esac
    case "${mem}" in
        bind)       NUMACTL_ARGS=(--cpunodebind="${NODES}" --membind="${NODES}") ;;
        interleave) NUMACTL_ARGS=(--cpunodebind="${NODES}" --interleave="${NODES}") ;;
        *) echo "[ERROR] unknown mem: ${mem}"; exit 1 ;;
    esac
}

# ファイル名に入れる条件のタグ（例 bind-close / bind-none-intlv）
flow_cond_tag() {
    local tag="bind-$1"
    [ "$2" = interleave ] && tag="${tag}-intlv"
    echo "${tag}"
}

# --- 掴んだ物理コア数の検査 ---------------------------------------------------
# AOBA の 6 月データ全損はこの検査が無かったため。論理 CPU ではなく物理コアで数える。
flow_check_cores() {
    local p=$1
    local nlogical ncores
    nlogical=$(numactl "${NUMACTL_ARGS[@]}" --show | awk '/^physcpubind:/{print NF-1}')
    ncores=$(numactl "${NUMACTL_ARGS[@]}" --show \
        | awk '/^physcpubind:/{for (i = 2; i <= NF; i++) print $i}' \
        | awk -F, 'NR == FNR {want[$1] = 1; next} !/^#/ && ($1 in want) {print $2}' \
            - <(lscpu -p=CPU,CORE) \
        | sort -u | wc -l)
    echo "numactl ${NUMACTL_ARGS[*]}"
    echo "bound: ${ncores} cores（論理 CPU ${nlogical}）/ P = ${p}"
    if [ "${ncores}" -lt "${p}" ]; then
        echo "[ERROR] 掴んだ物理コア ${ncores} が P=${p} より少ない。ジョブの割り当てを確認すること"
        exit 1
    fi
    if [ "${ncores}" -gt "${p}" ]; then
        echo "[note] P=${p} はノードの一部だけを使う（close で先頭から詰める）"
    fi
}

# --- スレッド固定の検査 -------------------------------------------------------
# plasmatest を小さいサイズで1回流し、各スレッドが動けるコアの集合を記録する。
# close のときは「P 本のスレッドが、互いに異なる P 個のコアに1本ずつ」でなければ中止する。
#   $1 = P, $2 = close | none, $3 = 出力ファイル
flow_check_affinity() {
    local p=$1 bind=$2 out=$3
    OMP_NUM_THREADS="${p}" OMP_DISPLAY_AFFINITY=true \
    OMP_AFFINITY_FORMAT='AFFINITY level=%L thread=%n cpus=%A' \
        numactl "${NUMACTL_ARGS[@]}" "${PLASMA_TEST}" dgeqrf --dim=2048x2048 --nb=128 --ib=32 \
        > "${out}" 2>&1 || true
    if ! grep -q '^AFFINITY level=1 ' "${out}"; then
        echo "[note] OMP_DISPLAY_AFFINITY が出力されない（GCC 8 以前の libgomp など）。/proc から読む"
        flow_proc_affinity "${p}" >> "${out}"
    fi

    local nthr nset
    nthr=$(grep '^AFFINITY level=1 ' "${out}" | awk '{print $3}' | sort -u | wc -l)
    nset=$(grep '^AFFINITY level=1 ' "${out}" | awk '{print $4}' | sort -u | wc -l)
    echo "affinity (bind=${bind}): threads=${nthr}, 異なるコア集合=${nset}, P=${p}"
    if [ "${bind}" = close ] && { [ "${nthr}" -ne "${p}" ] || [ "${nset}" -ne "${p}" ]; }; then
        echo "[ERROR] スレッドが1コアずつに固定されていない。${out} を確認すること"
        exit 1
    fi
}

# OMP_DISPLAY_AFFINITY は OpenMP 5.0（libgomp は GCC 9 以降）。AOBA の GCC 8.5 では出ないので、
# plasmatest を裏で流し、スレッドが P 本そろったところで各スレッドの Cpus_allowed_list を読む。
# 出力は OMP_DISPLAY_AFFINITY と同じ形（AFFINITY level=1 thread=<tid> cpus=<集合>）にそろえる。
#   $1 = P
flow_proc_affinity() {
    local p=$1 pid i n
    OMP_NUM_THREADS="${p}" \
        numactl "${NUMACTL_ARGS[@]}" "${PLASMA_TEST}" dgeqrf --dim=4096x4096 --nb=128 --ib=32 \
        > /dev/null 2>&1 &
    pid=$!
    for i in $(seq 1 300); do
        n=$(ls "/proc/${pid}/task" 2>/dev/null | wc -l)
        [ "${n}" -ge "${p}" ] && break
        sleep 0.1
    done
    for t in /proc/"${pid}"/task/*; do
        awk -v t="${t##*/}" '/^Cpus_allowed_list:/ {print "AFFINITY level=1 thread=" t " cpus=" $2}' \
            "${t}/status" 2>/dev/null
    done
    wait "${pid}" || true
}

# --- 計測条件の控え -----------------------------------------------------------
flow_meta() {
    echo "date: $(date '+%F %T')"
    echo "host: $(hostname)"
    echo "config: ${CONFIG}"
    echo "plasma-bench: $(git -C "${REPO}" rev-parse --short HEAD 2>/dev/null || echo '?')"
    echo "plasmatest: ${PLASMA_TEST}"
    ldd "${PLASMA_TEST}" | grep -E 'plasma|openblas|mkl|gomp|iomp'
    echo "SMT active: ${SMT}"
    echo "SOCKET: ${SOCKET} / NODES: ${NODES} (${CORES_PER_NODE} cores/node)"
    echo "OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS}"
    echo
    echo "# === lscpu ==="
    lscpu
    echo
    echo "# === lscpu -e（CACHE 列の最後が L3 の番号。close で何個の L3 を使うかが分かる）==="
    lscpu -e=CPU,CORE,SOCKET,NODE,CACHE,MAXMHZ
    echo
    echo "# === numactl -H ==="
    numactl -H
}

# --- 計測前後の状態（他ジョブの混入・クロック）--------------------------------
flow_snapshot() {
    echo "--- $1: $(date '+%F %T') ---"
    uptime
    numactl -H | grep free
    awk '/^cpu MHz/ {s += $4; n++} END {if (n) printf "mean cpu MHz = %.0f (%d cpus)\n", s / n, n}' \
        /proc/cpuinfo
}
