#!/usr/bin/env bash
# =============================================================================
# 0_preflight.sh — 計測の前の確認と「環境の表」の材料（EPYC 7543。計測はしない、2〜4分）
#
#   1. 他のジョブ（共用機）
#   2. 道具（perf・--call-graph dwarf が使えるか、debuginfod-find）
#   3. ライブラリ：plasmatest・libplasma・libplasma_core_blas・libopenblas・libiomp5・libgomp の
#      パス・md5・NEEDED・版の要求（readelf -V）、openblas_get_config、libgomp と libiomp5 の版、PLASMA の見出し
#   4. gompfwd.so を作る（mk_gompfwd.sh）。中継できない名前（libgomp に無いもの）があれば表示
#   5. 結び付き（LD_DEBUG=bindings）：iomp・gompnaive（LD_PRELOAD=libgomp だけ）・gomp（gompfwd）の3通りで、
#      OpenMP の名前（GOMP_*・omp_*・__kmpc_*・kmp_*）がどのライブラリに結び付いたかを数える
#   6. 実行中のプロセス（p = 32）：スレッドの数・各スレッドの CPU・maps に載っているランタイム（iomp・gomp）
#   7. 検算：size 1024 を --test=y で（iomp・gomp）。gompfwd で結果が変わらないこと
#   8. obshim：gomp でも計時の時刻と alloc の回数が取れるか（p = 32、size 1024、nb 32）
#   9. libgomp の機械語とシンボル（内部の関数を区分けするため。debuginfod で取れればシンボルつき）
#  10. 自前ビルド（~/Library/plasma/lib・~/Library/openblas）の記録だけ（使わない）
#
#   使い方（plasma-bench の直下で。ブランチ feat/libgomp-ab。S=scripts/hpc/epyc7543/campaigns/2026-11-24_libgomp-ab）:
#       MACHINE=epyc7543 bash $S/0_preflight.sh
#   出力：results/preflight_libgomp-ab_<時刻>/（log.txt と、機械語・結び付きのファイル）。ディレクトリごと devbox-lab の
#         ~/Download/epyc/ に持ち帰り、log.txt をチャットに渡す。
# =============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PB="${PLASMA_BENCH:-$PWD}"
MACHINE=${MACHINE:?MACHINE=epyc7543 を付ける}
export MACHINE
OUT="${PB}/results/preflight_libgomp-ab_$(date +%Y%m%d_%H%M%S)"   # results/ は git の管理外
mkdir -p "${OUT}"
exec > >(tee "${OUT}/log.txt") 2>&1
echo "# preflight（libgomp の比較）$(date '+%F %T') host=$(hostname) user=$(id -un) kernel=$(uname -r) MACHINE=${MACHINE}"
echo "# 出力: ${OUT}"

# shellcheck source=/dev/null
source "${PB}/scripts/hpc/lib/common.sh"
hpc_setup
# shellcheck source=/dev/null
source "${HERE}/rt.sh"
GOMPFWD="${HERE}/gompfwd/gompfwd.so"
SO="${HERE}/shim/obshim.so"
# readelf -V の「Version needs」から、OpenMP のランタイムへの版の要求を「ファイル: 版の名前」で出す
VN_AWK='/Version needs section/ {on=1} /Version definition section/ {on=0}
  on && /File:/ {for (i = 1; i <= NF; i++) if ($i == "File:") f = $(i + 1)}
  on && /Name:/ && f ~ /iomp|gomp/ {for (i = 1; i <= NF; i++) if ($i == "Name:") print "    " f ": " $(i + 1)}'

echo; echo "## 1. 他のジョブ"
uptime
ps -eo user:20,pcpu,etime,args --sort=-pcpu | head -6 | cut -c1-160
echo "other_users_cpu_pct=$(ps -eo user:20,pcpu | awk -v me="$(id -un)" 'NR>1 && $1!=me {s+=$2} END{printf "%.0f", s}')"

echo; echo "## 2. 道具"
for c in perf strace gcc python3 numactl objdump readelf nm debuginfod-find dpkg-query; do printf '%-16s ' "$c"; command -v "$c" || echo "なし"; done
perf --version 2>&1 | head -1
echo "perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null) kptr_restrict=$(cat /proc/sys/kernel/kptr_restrict 2>/dev/null)"
echo "DEBUGINFOD_URLS=${DEBUGINFOD_URLS:-（未設定）}"

echo; echo "## 3. ライブラリ"
echo "PLASMA_TEST=${PLASMA_TEST}"
echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}"
echo "### plasmatest の NEEDED・RUNPATH"
readelf -d "${PLASMA_TEST}" | grep -E 'NEEDED|RPATH|RUNPATH'
echo "### ldd"
ldd "${PLASMA_TEST}" | grep -E 'plasma|openblas|mkl|gomp|iomp|gfortran'
LIBPLASMA=$(ldd "${PLASMA_TEST}" | awk '/libplasma\.so/ {print $3; exit}')
LIBCORE=$(ldd "${PLASMA_TEST}" | awk '/libplasma_core_blas/ {print $3; exit}')
LIBOB=$(ldd "${PLASMA_TEST}" | awk '/libopenblas/ {print $3; exit}')
LIBIOMP=$(ldd "${PLASMA_TEST}" | awk '/libiomp5/ {print $3; exit}')
LIBGOMP=$(ldd "${PLASMA_TEST}" | awk '/libgomp\.so/ {print $3; exit}')
for f in "${PLASMA_TEST}" "${LIBPLASMA}" "${LIBCORE}" "${LIBOB}" "${LIBIOMP}" "${LIBGOMP}"; do
    [ -n "${f}" ] || continue
    r=$(readlink -f "${f}")
    echo "### ${f} -> ${r}"
    echo "md5 $(md5sum "${r}" | cut -d' ' -f1)  size $(stat -c %s "${r}")  mtime $(stat -c %y "${r}" | cut -d. -f1)  owner $(stat -c %U "${r}")"
    readelf -d "${r}" | grep -E 'NEEDED|SONAME' | sed -E 's/.*\[(.*)\]/  \1/' | tr '\n' ' '; echo
    echo "  版の要求（Version needs のうち OpenMP のランタイム）:"
    readelf -V "${r}" | awk "${VN_AWK}" | sort -u | head -20
done
echo "### openblas_get_config"
python3 -c "import ctypes,sys; l=ctypes.CDLL(sys.argv[1]); l.openblas_get_config.restype=ctypes.c_char_p; print(l.openblas_get_config().decode())" "${LIBOB}"
echo "### libgomp の版"
dpkg-query -S "$(readlink -f "${LIBGOMP}")" 2>/dev/null
dpkg-query -W -f='${Package} ${Version}\n' 'libgomp1*' 2>/dev/null
strings -a "$(readlink -f "${LIBGOMP}")" | grep -m2 'GCC: ('
echo "### libiomp5 の版"
strings -a "$(readlink -f "${LIBIOMP}")" | grep -m3 -iE 'Intel\(R\) OMP|LLVM OMP|OpenMP.*version|Compiler.*20[0-9][0-9]'
echo "### PLASMA の見出し（plasmatest の最初の行）"
OMP_NUM_THREADS=1 "${PLASMA_TEST}" dgeqrf --dim=128x128 --nb=32 --ib=8 --test=n 2>&1 | head -12

echo; echo "## 4. gompfwd.so"
bash "${HERE}/mk_gompfwd.sh" "${PLASMA_TEST}" "${HERE}/gompfwd"
cp "${HERE}"/gompfwd/{gompfwd.c,gompfwd_syms.txt,gompfwd_missing.txt,omp_refs.txt} "${OUT}/" 2>/dev/null

echo; echo "## 5. 結び付き（LD_DEBUG=bindings、p = 4、size 512）"
hpc_select_nodes 32
hpc_set_cond close bind
for rt in iomp gompnaive gomp; do
    rt_set "${rt}" || continue
    rm -f "${OUT}/bind_${rt}".*
    OMP_NUM_THREADS=4 LD_DEBUG=bindings LD_DEBUG_OUTPUT="${OUT}/bind_${rt}" \
        numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" "${PLASMA_TEST}" dgeqrf --dim=512x512 --nb=64 --ib=16 --test=n > /dev/null 2>&1
    cat "${OUT}/bind_${rt}".* 2>/dev/null | sed -E 's/^ *[0-9]+: *//' \
        | grep -E "symbol \`(GOMP_|omp_|__kmpc_|kmp_|__kmp)" | sort -u | gzip -c > "${OUT}/bind_${rt}.txt.gz"
    rm -f "${OUT}/bind_${rt}".[0-9]*
    echo "### ${rt}（LD_PRELOAD=$(rt_preload "")）：参照するファイル → 結び付いた先（OpenMP の名前の数）"
    zcat "${OUT}/bind_${rt}.txt.gz" | sed -E 's/^binding file ([^ ]+) \[[0-9]+\] to ([^ ]+) \[[0-9]+\].*/\1 -> \2/' \
        | awk '{n=split($1,a,"/"); m=split($3,b,"/"); print a[n], "->", b[m]}' | sort | uniq -c
    echo "  plasma・openblas・plasmatest から libiomp5 に結び付いた名前（gomp では 0 のはず）:"
    zcat "${OUT}/bind_${rt}.txt.gz" | grep -E '^binding file [^ ]*(plasma|openblas)[^ ]* .* to [^ ]*libiomp5' \
        | sed -E "s/.*symbol \`([^']+)'.*/\1/" | sort -u | tr '\n' ' ' | cut -c1-400; echo
    echo "  plasma・openblas・plasmatest から libgomp に結び付いた名前（iomp では 0 のはず）:"
    zcat "${OUT}/bind_${rt}.txt.gz" | grep -E '^binding file [^ ]*(plasma|openblas)[^ ]* .* to [^ ]*libgomp' \
        | sed -E "s/.*symbol \`([^']+)'.*/\1/" | sort -u | tr '\n' ' ' | cut -c1-400; echo
done

echo; echo "## 6. 実行中のプロセス（p = 32、size 4096・nb 32）"
for rt in iomp gomp; do
    rt_proc_check "${rt}" 32 "${OUT}/proc_${rt}.txt"
    echo "### ${rt}"
    grep -E '^# |^threads=|^distinct_cpus=' "${OUT}/proc_${rt}.txt"
    sed -n '/^## maps/,/^## 各スレッド/p' "${OUT}/proc_${rt}.txt" | grep -v '^##'
    echo "  スレッドの CPU（先頭 4 本）:"; sed -n '/^## 各スレッド/,/^distinct/p' "${OUT}/proc_${rt}.txt" | grep -v -E '^##|^distinct' | head -4 | sed 's/^/    /'
done
echo "（threads が 32 なら、スレッドを作ったランタイムは1つ。両方が作っていれば 32 を超える。distinct_cpus が 32 なら1スレッド1コア）"
echo "### gomp の OMP_DISPLAY_AFFINITY（libgomp が自分で出す。p = 32）"
rt_set gomp && OMP_NUM_THREADS=32 OMP_DISPLAY_AFFINITY=true OMP_AFFINITY_FORMAT='AFFINITY thread=%n cpus=%A' \
    numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" "${PLASMA_TEST}" dgeqrf --dim=1024x1024 --nb=64 --ib=16 --test=n 2>&1 \
    | grep AFFINITY | sort -t= -k2 -n | awk '{print}' | head -40 | tr '\n' ';' | cut -c1-900; echo

echo; echo "## 7. 検算（p = 32、size 1024、nb 64、ib 16、--test=y）"
for rt in iomp gomp; do
    rt_set "${rt}" || continue
    echo "### ${rt}"
    OMP_NUM_THREADS=32 numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" \
        "${PLASMA_TEST}" dgeqrf --dim=1024x1024 --nb=64 --ib=16 --test=y 2>&1 | tail -4
done

echo; echo "## 8. obshim（pass、p = 32、size 1024、nb 32、ib 16）"
gcc -O2 -Wall -fPIC -shared -o "${SO}" "${HERE}/shim/obshim.c" -ldl -lpthread && echo "build ok: ${SO}"
for rt in iomp gomp; do
    rt_set "${rt}" || continue
    o=$(mktemp)
    OMP_NUM_THREADS=32 numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "${SO}")" OBSHIM_MODE=pass OBSHIM_OUT="${o}" \
        "${PLASMA_TEST}" dgeqrf --dim=1024x1024 --nb=32 --ib=16 --test=n | awk -v rt="${rt}" '$3 ~ /^[0-9]+\.[0-9]+$/ && $4 ~ /^[0-9]+\.[0-9]+$/ {print rt, "Time=" $3 " Gflop/s=" $4; exit}'
    python3 - "${o}" "${rt}" <<'EOF'
import json, sys
for l in open(sys.argv[1]):
    d = json.loads(l)
    if d["type"] == "meta":
        print(sys.argv[2], "meta:", {k: d[k] for k in ("tsc_hz", "threads_seen", "stamps", "distinct_buffers", "real_alloc_found")})
    elif d["type"] == "phase" and d["phase"] == 1:
        print(sys.argv[2], f"phase1: n_alloc={d['n_alloc']} cyc/alloc={d['cyc_alloc'] / max(d['n_alloc'], 1):.0f} cyc/free={d['cyc_free'] / max(d['n_free'], 1):.0f}")
EOF
    rm -f "${o}"
done
echo "（stamps が 2 以上・n_alloc が 0 でなければ、gomp でも計時の区間と alloc が取れている）"

echo; echo "## 9. libgomp の機械語とシンボル"
G=$(readlink -f "${LIBGOMP}")
readelf -n "${G}" | grep -i 'build id'
BID=$(readelf -n "${G}" | awk '/Build ID/ {print $3}')
readelf --string-dump=.gnu_debuglink "${G}" 2>/dev/null | grep -v '^$' | tail -n +2
DBG=""
[ -n "${BID}" ] && [ -f "/usr/lib/debug/.build-id/${BID:0:2}/${BID:2}.debug" ] && DBG="/usr/lib/debug/.build-id/${BID:0:2}/${BID:2}.debug"
if [ -z "${DBG}" ] && command -v debuginfod-find > /dev/null && [ -n "${BID}" ]; then
    DBG=$(DEBUGINFOD_URLS="${DEBUGINFOD_URLS:-https://debuginfod.ubuntu.com}" timeout 60 debuginfod-find debuginfo "${BID}" 2>/dev/null || true)
fi
if [ -n "${DBG}" ] && [ -f "${DBG}" ]; then
    echo "debug のシンボル: ${DBG}"
    nm -n "${DBG}" > "${OUT}/libgomp_syms.txt" 2>/dev/null
    echo "  関数の数 $(grep -c ' [tT] ' "${OUT}/libgomp_syms.txt")。gomp_mutex_lock_slow: $(grep -E ' gomp_mutex_lock_slow$' "${OUT}/libgomp_syms.txt")"
else
    echo "debug のシンボルは取れなかった（機械語から区分けする。不老・弐と同じ方法）"
fi
objdump -d --no-show-raw-insn "${G}" | gzip -c > "${OUT}/libgomp.dis.gz"
nm -D -n "${G}" > "${OUT}/libgomp_dynsym.txt"
readelf -lW "${G}" > "${OUT}/libgomp_loads.txt"
echo "機械語: ${OUT}/libgomp.dis.gz（$(zcat "${OUT}/libgomp.dis.gz" | wc -l) 行）。futex（syscall）の箇所: $(zcat "${OUT}/libgomp.dis.gz" | grep -c 'syscall')、lock cmpxchg: $(zcat "${OUT}/libgomp.dis.gz" | grep -c 'lock cmpxchg')、pause: $(zcat "${OUT}/libgomp.dis.gz" | grep -c 'pause')"

echo; echo "## 2'. perf --call-graph dwarf の試し（gomp、p = 32、size 1024、nb 32）"
if command -v perf > /dev/null; then
    rt_set gomp
    tmp=$(mktemp -d)
    OMP_NUM_THREADS=32 perf record -k CLOCK_MONOTONIC -e cpu-clock -F 499 --call-graph dwarf,4096 -o "${tmp}/g.data" -- \
        numactl "${NUMACTL_ARGS[@]}" env LD_PRELOAD="$(rt_preload "")" "${PLASMA_TEST}" dgeqrf --dim=1024x1024 --nb=32 --ib=16 --test=n > /dev/null 2> "${tmp}/rec.txt"
    echo "perf record exit=$?  $(grep -v WARNING "${tmp}/rec.txt" | tail -1)"
    perf script -i "${tmp}/g.data" -F comm,tid,time,ip,sym,symoff,dso 2>/dev/null > "${tmp}/g.txt"
    echo "試料 $(grep -cE '^\S' "${tmp}/g.txt")、枠の行 $(grep -cE '^\s+[0-9a-f]+ ' "${tmp}/g.txt")。libgomp が葉の試料の例:"
    awk 'BEGIN{RS=""} /libgomp/ {print; n++} n>=2 {exit}' "${tmp}/g.txt" | head -16
    rm -rf "${tmp}"
fi

echo; echo "## 10. 自前ビルド（記録だけ。今回は使わない）"
ls -la "${HOME}/Library/plasma/lib" "${HOME}/Library/openblas/lib" 2>&1 | head -30
for f in "${HOME}"/Library/plasma/lib/libplasma*.so.*[0-9] "${HOME}"/Library/openblas/lib/libopenblas*.so; do
    [ -f "${f}" ] || continue
    echo "### ${f}  md5 $(md5sum "${f}" | cut -d' ' -f1)"
    readelf -d "${f}" | grep -E 'NEEDED|SONAME' | sed -E 's/.*\[(.*)\]/  \1/' | tr '\n' ' '; echo
    readelf -V "${f}" | awk "${VN_AWK}" | sort -u | head -8
done
echo "### ~/Library/plasma/lib を先に置いたときの ldd"
LD_LIBRARY_PATH="${HOME}/Library/plasma/lib:${LD_LIBRARY_PATH:-}" ldd "${PLASMA_TEST}" | grep -E 'plasma|openblas|mkl|gomp|iomp'

echo; echo "# preflight 終わり。${OUT} を持ち帰り、log.txt をチャットに渡す"
