#!/usr/bin/env bash
# rt.sh — OpenMP のランタイムと待ち方の条件（0_preflight.sh・1_run.sh・2_sweep_gomp.sh から source する）
#
#   2026-11-24_libgomp-ab の rt.sh に、待ち方を足したもの。
#
#   名前   ランタイム  待ち方            環境変数（待ち方の変数はどの条件でも一度 unset してから設定する）
#   idef   libiomp5   既定（約 200 ms 回る）  KMP_AFFINITY=granularity=core,compact               （前回の iomp と同じ）
#   ibt0   libiomp5   すぐ眠る              上に KMP_BLOCKTIME=0
#   gdef   libgomp    既定（短く回って眠る）   OMP_PLACES=cores OMP_PROC_BIND=close、LD_PRELOAD gompfwd.so（前回の gomp と同じ）
#   gact   libgomp    回り続ける            上に OMP_WAIT_POLICY=active
#   iomp・gomp は idef・gdef の別名（前回の名前）。gompnaive は preflight の確かめ用（LD_PRELOAD=libgomp.so.1 だけ。替わらない）
#
#   OMP_WAIT_POLICY は libiomp5 も読むので I では unset。KMP_BLOCKTIME は G では unset。GOMP_SPINCOUNT はどれでも unset。
#   固定はランタイムに合わせてここで決める（common.sh の hpc_set_cond は plasmatest の ldd に libiomp5 があると
#   KMP_AFFINITY を選ぶので、gompfwd で libgomp に替えたときは固定が効かない。hpc_set_cond の後に rt_set を呼ぶ）
#
# rt_set <rt>：RT_PRELOAD（LD_PRELOAD に足すもの）と、固定・待ち方の環境変数を設定する。GOMPFWD を先に設定しておく。
RT_ALL="idef ibt0 gdef gact"
rt_set() {
    unset OMP_PLACES OMP_PROC_BIND KMP_AFFINITY GOMP_CPU_AFFINITY
    unset OMP_WAIT_POLICY KMP_BLOCKTIME GOMP_SPINCOUNT KMP_LIBRARY
    case "$1" in
        idef|iomp|ibt0)
            RT_PRELOAD=""
            export KMP_AFFINITY=granularity=core,compact
            if [ "$1" = ibt0 ]; then export KMP_BLOCKTIME=0; fi ;;
        gdef|gomp|gact)
            [ -f "${GOMPFWD:?GOMPFWD を設定する}" ] || { echo "[ERROR] gompfwd.so が無い: ${GOMPFWD}"; return 1; }
            RT_PRELOAD="${GOMPFWD}"
            export OMP_PLACES=cores OMP_PROC_BIND=close
            if [ "$1" = gact ]; then export OMP_WAIT_POLICY=active; fi ;;
        gompnaive)
            RT_PRELOAD="$(ldd "${PLASMA_TEST}" | awk '/libgomp\.so/ {print $3; exit}')"
            export OMP_PLACES=cores OMP_PROC_BIND=close ;;
        *) echo "[ERROR] unknown rt: $1"; return 1 ;;
    esac
}

# rt_env_line：今の固定・待ち方の環境変数を1行で（meta・preflight の記録用）
rt_env_line() {
    echo "OMP_PLACES=${OMP_PLACES:-} OMP_PROC_BIND=${OMP_PROC_BIND:-} KMP_AFFINITY=${KMP_AFFINITY:-} OMP_WAIT_POLICY=${OMP_WAIT_POLICY:-} KMP_BLOCKTIME=${KMP_BLOCKTIME:-} GOMP_SPINCOUNT=${GOMP_SPINCOUNT:-} RT_PRELOAD=${RT_PRELOAD:-}"
}

# rt_preload <obshim のパス または 空>：LD_PRELOAD の値（obshim を先に置く。obshim の omp_get_wtime は RTLD_NEXT で次＝gompfwd を呼ぶ）
rt_preload() {
    local s="$1"
    if [ -n "${s}" ] && [ -n "${RT_PRELOAD}" ]; then echo "${s}:${RT_PRELOAD}"
    elif [ -n "${s}" ]; then echo "${s}"
    else echo "${RT_PRELOAD}"; fi
}

# rt_proc_check <rt> <p> <出力ファイル> [obshim]：plasmatest（size 4096・nb 32・ib 16）を裏で流し、スレッドが p 本そろったところで
#   スレッドの数、各スレッドの Cpus_allowed_list、/proc/<pid>/maps に載っている OpenMP のランタイム、/proc/<pid>/environ の待ち方を書く。
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
    # 読んでいる途中で plasmatest が終わると数が欠けるので、止めてから /proc を1回だけ読む
    kill -STOP "${pid}" 2>/dev/null || true
    local tlines
    tlines=$(for t in /proc/"${pid}"/task/*; do
        printf '%s %s cpus=%s state=%s\n' "${t##*/}" "$(cat "${t}/comm" 2>/dev/null)" \
            "$(awk '/^Cpus_allowed_list:/ {print $2}' "${t}/status" 2>/dev/null)" \
            "$(awk '/^State:/ {print $2}' "${t}/status" 2>/dev/null)"
    done)
    {
        echo "# rt=${rt} p=${p} pid=${pid} LD_PRELOAD=$(rt_preload "${shim}")"
        echo "# env: $(rt_env_line)"
        echo "## environ（固定・待ち方の変数。プロセスに渡ったもの）"
        tr '\0' '\n' 2>/dev/null < "/proc/${pid}/environ" | grep -E '^(OMP_|KMP_|GOMP_|LD_PRELOAD=)' | sort || true
        echo "threads=$(ls "/proc/${pid}/task" 2>/dev/null | wc -l)"
        echo "## maps（OpenMP のランタイムと中継）"
        grep -E 'libiomp5|libgomp|gompfwd|obshim' "/proc/${pid}/maps" 2>/dev/null | awk '{print $2, $6}' | sort -u || true
        echo "## 各スレッドの CPU（Cpus_allowed_list）と状態"
        echo "${tlines}"
        echo "distinct_cpus=$(echo "${tlines}" | sed -nE 's/.* cpus=([^ ]+) .*/\1/p' | sort -u | wc -l)"
    } > "${out}"
    kill "${pid}" 2>/dev/null || true; kill -CONT "${pid}" 2>/dev/null || true; wait "${pid}" 2>/dev/null || true
}
