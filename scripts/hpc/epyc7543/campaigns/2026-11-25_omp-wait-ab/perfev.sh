#!/usr/bin/env bash
# perfev.sh — perf のイベントの組 A・B（0_preflight.sh・1_run.sh から source する）
#
#   組 A：task-clock（周期 100000 ns ＝ 100 µs）、cycles（1000003）、instructions（1000003）
#   組 B：cycles（1000003）と Zen3 の取り寄せ先 4 つ（ls_dmnd_fills_from_sys.{lcl_l2,int_cache,ext_cache_local,mem_io_local}、10007）
#         PERF_B=generic のときは cycles（1000003）・L1-dcache-load-misses・L1-dcache-loads・cache-misses・cache-references（10007）
#   周期はイベントごとに固定（-c と同じ意味。項 period= で付ける）。数は「試料の period の合計」で出す。
#   組を分けるのは、ハードウェアのカウンタ（Zen3 は 6 本）に収め、時分割で数がぶれないようにするため。
#
#   Zen3 のイベントは perf list --details から event・umask を読み、cpu/event=..,umask=..,period=..,name=../ の形で渡す
#   （名前に点があると項として渡せない perf があるため）。perf list に名前が無ければ止める（PERF_B=generic で替える）。
#
# perfev_resolve：PERF_A_REC・PERF_B_REC（perf record の -e の並び）、PERF_A_STAT・PERF_B_STAT（perf stat の -e）、
#                 PERF_B_KIND（zen3｜generic）を設定する。失敗したら 1 を返す。
PERF_B=${PERF_B:-zen3}
ZEN3_FILLS="lcl_l2 int_cache ext_cache_local mem_io_local"

# perfev_enc <イベント名>：perf list --details から "event=0x..,umask=0x.." を取り出す（無ければ空）
perfev_enc() {
    perf list --details "$1" 2>/dev/null | tr ' \t' '\n\n' | grep -E '^(cpu/)?[a-z0-9_=,]*event=0x[0-9a-fA-F]+' | head -1 \
        | sed -E 's#^cpu/##; s#/$##' | tr -d '[:space:]'
}

perfev_resolve() {
    PERF_A_REC=(-e "task-clock/period=100000/" -e "cycles/period=1000003/" -e "instructions/period=1000003/")
    PERF_A_STAT="task-clock,cycles,instructions"
    PERF_B_REC=(-e "cycles/period=1000003/")
    PERF_B_STAT="cycles"
    case "${PERF_B}" in
        zen3)
            local f enc
            for f in ${ZEN3_FILLS}; do
                enc=$(perfev_enc "ls_dmnd_fills_from_sys.${f}")
                if [ -z "${enc}" ]; then
                    echo "[ERROR] perf list に ls_dmnd_fills_from_sys.${f} が無い（または event・umask が読めない）。PERF_B=generic で替える" >&2
                    return 1
                fi
                PERF_B_REC+=(-e "cpu/${enc},period=10007,name=fill_${f}/")
                PERF_B_STAT="${PERF_B_STAT},cpu/${enc},name=fill_${f}/"
            done
            PERF_B_KIND=zen3 ;;
        generic)
            local e
            for e in L1-dcache-load-misses L1-dcache-loads cache-misses cache-references; do
                PERF_B_REC+=(-e "${e}/period=10007/")
                PERF_B_STAT="${PERF_B_STAT},${e}"
            done
            PERF_B_KIND=generic ;;
        *) echo "[ERROR] unknown PERF_B: ${PERF_B}" >&2; return 1 ;;
    esac
}
