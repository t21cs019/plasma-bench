#!/usr/bin/env bash
# rt.sh — OpenMP のランタイムの条件（0_preflight.sh・1_run.sh から source する）
#
#   iomp  いままでと同じ。plasmatest・libplasma が直接リンクしている libiomp5 が使われる。
#         固定は KMP_AFFINITY=granularity=core,compact（plasma-bench の hpc_set_cond と同じ。OMP_PLACES=cores だと
#         libiomp5 で全スレッドが CPU 0 に集まった、11-01）
#   gomp  同じバイナリのまま、gompfwd.so（mk_gompfwd.sh）を LD_PRELOAD して GOMP_*・omp_* を libgomp に結び付ける。
#         libgomp は OpenBLAS が NEEDED に持つもの（/lib/x86_64-linux-gnu/libgomp.so.1）。
#         固定は OMP_PLACES=cores OMP_PROC_BIND=close（不老・弐と同じ）
#   gompnaive  LD_PRELOAD=libgomp.so.1 だけ（preflight で「これでは替わらない」ことを確かめる用。本計測では使わない）
#
#   待ち方（OMP_WAIT_POLICY・KMP_BLOCKTIME・GOMP_SPINCOUNT）はどちらも既定のまま（不老・弐・過去の EPYC と同じ）。
#
# rt_set <rt>：RT_PRELOAD（LD_PRELOAD に足すもの）と固定の環境変数を設定する。GOMPFWD を先に設定しておく。
rt_set() {
    unset OMP_PLACES OMP_PROC_BIND KMP_AFFINITY GOMP_CPU_AFFINITY
    case "$1" in
        iomp)
            RT_PRELOAD=""
            export KMP_AFFINITY=granularity=core,compact ;;
        gomp)
            [ -f "${GOMPFWD:?GOMPFWD を設定する}" ] || { echo "[ERROR] gompfwd.so が無い: ${GOMPFWD}"; return 1; }
            RT_PRELOAD="${GOMPFWD}"
            export OMP_PLACES=cores OMP_PROC_BIND=close ;;
        gompnaive)
            RT_PRELOAD="$(ldd "${PLASMA_TEST}" | awk '/libgomp\.so/ {print $3; exit}')"
            export OMP_PLACES=cores OMP_PROC_BIND=close ;;
        *) echo "[ERROR] unknown rt: $1"; return 1 ;;
    esac
}

# rt_preload <obshim のパス または 空>：LD_PRELOAD の値（obshim を先に置く。obshim の omp_get_wtime は RTLD_NEXT で次＝gompfwd を呼ぶ）
rt_preload() {
    local s="$1"
    if [ -n "${s}" ] && [ -n "${RT_PRELOAD}" ]; then echo "${s}:${RT_PRELOAD}"
    elif [ -n "${s}" ]; then echo "${s}"
    else echo "${RT_PRELOAD}"; fi
}

# rt_proc_check <rt> <p> <出力ファイル> [obshim]：plasmatest（size 4096・nb 32・ib 16）を裏で流し、スレッドが p 本そろったところで
#   スレッドの数、各スレッドの Cpus_allowed_list、/proc/<pid>/maps に載っている OpenMP のランタイムを書く。
#   両方のランタイムがスレッドを作っていれば、スレッドの数が p を超える。
rt_proc_check() {
    local rt=$1 p=$2 out=$3 shim=${4:-} pid n i
    rt_set "${rt}" || return 1
    OMP_NUM_THREADS="${p}" numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "${shim}")" \
        "${PLASMA_TEST}" dgeqrf --dim=4096x4096 --nb=32 --ib=16 --test=n > /dev/null 2>&1 &
    pid=$!
    n=0
    for i in $(seq 1 100); do
        sleep 0.05
        [ -d "/proc/${pid}" ] || break
        n=$(ls "/proc/${pid}/task" 2>/dev/null | wc -l)
        [ "${n}" -ge "${p}" ] && break
    done
    sleep 0.3
    {
        echo "# rt=${rt} p=${p} pid=${pid} LD_PRELOAD=$(rt_preload "${shim}")"
        echo "# env: OMP_PLACES=${OMP_PLACES:-} OMP_PROC_BIND=${OMP_PROC_BIND:-} KMP_AFFINITY=${KMP_AFFINITY:-}"
        echo "threads=$(ls "/proc/${pid}/task" 2>/dev/null | wc -l)"
        echo "## maps（OpenMP のランタイムと中継）"
        grep -E 'libiomp5|libgomp|gompfwd|obshim' "/proc/${pid}/maps" 2>/dev/null | awk '{print $2, $6}' | sort -u
        echo "## 各スレッドの CPU（Cpus_allowed_list）と状態"
        for t in /proc/"${pid}"/task/*; do
            printf '%s %s cpus=%s state=%s\n' "${t##*/}" "$(cat "${t}/comm" 2>/dev/null)" \
                "$(awk '/^Cpus_allowed_list:/ {print $2}' "${t}/status" 2>/dev/null)" \
                "$(awk '/^State:/ {print $2}' "${t}/status" 2>/dev/null)"
        done
        echo "distinct_cpus=$(for t in /proc/"${pid}"/task/*; do awk '/^Cpus_allowed_list:/ {print $2}' "${t}/status" 2>/dev/null; done | sort -u | wc -l)"
    } > "${out}"
    kill "${pid}" 2>/dev/null; wait "${pid}" 2>/dev/null || true
}
