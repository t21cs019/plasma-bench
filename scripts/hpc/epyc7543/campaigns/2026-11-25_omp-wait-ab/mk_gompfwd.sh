#!/usr/bin/env bash
# =============================================================================
# mk_gompfwd.sh — libgomp への中継ライブラリ gompfwd.so を作る（2026-11-24）
#
# なぜ要るか：研究室の EPYC 7543 の plasmatest・/usr/local/lib の libplasma は libiomp5 に直接リンクされていて、
#   GOMP_task などを libiomp5 の版（"VERSION"）つきで参照している（11-24 の LD_DEBUG の `[VERSION]`）。
#   glibc は版の名前が合わない定義を飛ばすので、LD_PRELOAD=libgomp.so.1 だけでは libgomp に替わらない。
#   版を持たない共有ライブラリの定義は、版つきの参照も受け付ける。そこで、版を持たない gompfwd.so に
#   同じ名前の関数を置き、中身は libgomp の本物への jmp 1命令にする（引数・戻り値に触らない）。
#
# 何を中継するか：plasmatest・libplasma・libplasma_core_blas・libopenblas が参照する OpenMP の関数
#   （GOMP_*・omp_*）のうち、libgomp が定義しているもの。libgomp に無いもの（__kmpc_*・kmp_* など）は
#   中継できないので一覧を出して止まる。
# 解決は遅延（初めて呼ばれたとき dlopen("libgomp.so.1") ＋ dlsym）。libopenblas のコンストラクタが
#   gompfwd の初期化より先に omp_* を呼んでも動くようにするため。
#
#   使い方：bash mk_gompfwd.sh <plasmatest> <出力ディレクトリ>
#   出力：<dir>/gompfwd.so、<dir>/gompfwd.c、<dir>/gompfwd_syms.txt（中継する名前）、<dir>/gompfwd_missing.txt
# =============================================================================
set -euo pipefail
EXE=${1:?plasmatest のパス}
OUT=${2:?出力ディレクトリ}
mkdir -p "${OUT}"

objs=("${EXE}")
while read -r lib; do objs+=("${lib}"); done < <(ldd "${EXE}" | awk '/libplasma|libopenblas/ {print $3}')
GOMP=$(ldd "${EXE}" | awk '/libgomp\.so/ {print $3; exit}')
[ -n "${GOMP}" ] || { echo "[mk_gompfwd] ldd に libgomp が無い"; exit 1; }

# 参照している OpenMP の関数（nm -D の U。binutils によっては name@VERSION と出るので @ 以降を落とす）
for o in "${objs[@]}"; do nm -D --undefined-only "${o}" | awk '{print $NF}'; done \
    | sed 's/@.*//' | grep -E '^(GOMP_|omp_|__kmpc_|kmp_|ompt_|__kmp)' | sort -u > "${OUT}/omp_refs.txt"
nm -D --defined-only "${GOMP}" | awk '{print $NF}' | sed 's/@.*//' | sort -u > "${OUT}/libgomp_defs.txt"
comm -12 "${OUT}/omp_refs.txt" "${OUT}/libgomp_defs.txt" > "${OUT}/gompfwd_syms.txt"
comm -23 "${OUT}/omp_refs.txt" "${OUT}/libgomp_defs.txt" > "${OUT}/gompfwd_missing.txt"
echo "[mk_gompfwd] 参照 $(wc -l < "${OUT}/omp_refs.txt") 個、中継 $(wc -l < "${OUT}/gompfwd_syms.txt") 個、libgomp に無い $(wc -l < "${OUT}/gompfwd_missing.txt") 個（libgomp: ${GOMP}）"
if [ -s "${OUT}/gompfwd_missing.txt" ]; then
    echo "[mk_gompfwd] libgomp に無い名前（中継できない。このままでは libiomp5 に残る）:"; sed 's/^/    /' "${OUT}/gompfwd_missing.txt"
fi

n=$(wc -l < "${OUT}/gompfwd_syms.txt")
{
cat <<'EOF'
/* mk_gompfwd.sh が生成。版を持たない中継：各関数は jmp *gf_ptr[i]。gf_ptr[i] の初期値は遅延解決の入口。 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
EOF
echo "#define GF_N ${n}"
echo "static const char *gf_name[GF_N] = {"
sed 's/.*/    "&",/' "${OUT}/gompfwd_syms.txt"
echo "};"
echo "__attribute__((visibility(\"hidden\"))) void *gf_ptr[GF_N] = {"
i=0; while read -r s; do echo "    (void *)gf_stub_${i},"; i=$((i + 1)); done < "${OUT}/gompfwd_syms.txt"
echo "};"
cat <<'EOF'
__attribute__((visibility("hidden"))) void *gf_resolve(int i) {
    static void *h;
    if (!h) h = dlopen("libgomp.so.1", RTLD_NOW | RTLD_GLOBAL);
    void *p = h ? dlsym(h, gf_name[i]) : NULL;
    if (!p) { fprintf(stderr, "[gompfwd] %s を libgomp から解決できない: %s\n", gf_name[i], dlerror()); abort(); }
    __atomic_store_n(&gf_ptr[i], p, __ATOMIC_RELEASE);
    if (getenv("GOMPFWD_VERBOSE")) {
        Dl_info di; dladdr(p, &di);
        fprintf(stderr, "[gompfwd] %s -> %s\n", gf_name[i], di.dli_fname ? di.dli_fname : "?");
    }
    return p;
}
/* 遅延解決の入口：引数レジスタ（rdi rsi rdx rcx r8 r9、可変長引数の rax）を退避して gf_resolve(i) を呼び、本物へ jmp。
   OpenMP の API・GOMP_* に浮動小数の引数を取るものは無いので xmm は退避しない。 */
__asm__(
"    .text\n"
"    .type gf_lazy,@function\n"
"gf_lazy:\n"
"    push %rdi\n    push %rsi\n    push %rdx\n    push %rcx\n    push %r8\n    push %r9\n    push %rax\n"
"    mov %r11d, %edi\n"
"    call gf_resolve\n"
"    mov %rax, %r11\n"
"    pop %rax\n    pop %r9\n    pop %r8\n    pop %rcx\n    pop %rdx\n    pop %rsi\n    pop %rdi\n"
"    jmp *%r11\n"
"    .size gf_lazy,.-gf_lazy\n"
EOF
i=0
while read -r s; do
    printf '"    .globl %s\\n    .type %s,@function\\n%s:\\n    jmp *gf_ptr+%d(%%rip)\\n    .size %s,.-%s\\n"\n' "${s}" "${s}" "${s}" $((8 * i)) "${s}" "${s}"
    printf '"    .type gf_stub_%d,@function\\ngf_stub_%d:\\n    mov $%d, %%r11d\\n    jmp gf_lazy\\n"\n' "${i}" "${i}" "${i}"
    i=$((i + 1))
done < "${OUT}/gompfwd_syms.txt"
echo ");"
} > "${OUT}/gompfwd.c.tmp"
# gf_stub_i を配列の初期化より前に宣言する
{
    sed -n '1,/^#define GF_N/p' "${OUT}/gompfwd.c.tmp"
    i=0; while [ "${i}" -lt "${n}" ]; do echo "extern char gf_stub_${i}[];"; i=$((i + 1)); done
    sed '1,/^#define GF_N/d' "${OUT}/gompfwd.c.tmp"
} > "${OUT}/gompfwd.c"
rm -f "${OUT}/gompfwd.c.tmp"
# 版の情報を付けない（-Wl,--version-script を使わない）。-Bsymbolic は付けない（obshim の omp_get_wtime を先に置けるように）
gcc -O2 -Wall -fPIC -shared -o "${OUT}/gompfwd.so" "${OUT}/gompfwd.c" -ldl
if readelf -V "${OUT}/gompfwd.so" | grep -q 'Version definition'; then
    echo "[mk_gompfwd] gompfwd.so に版の定義が付いた（想定外）"; exit 1
fi
echo "[mk_gompfwd] ok: ${OUT}/gompfwd.so（md5 $(md5sum "${OUT}/gompfwd.so" | cut -d' ' -f1)）"
