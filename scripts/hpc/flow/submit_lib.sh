#!/usr/bin/env bash
# =============================================================================
# submit_lib.sh — 不老・弐の投入スクリプト（campaigns/<日付>_<名前>/submit_*.sh）から source する関数
#
#   * FLOW_MAINT（例 "2026-11-02 08:00 +0900"）を source の前に設定しておくと、walltime が
#     そのメンテナンスにかかる場合は縮める。メンテナンスにかかるジョブは開始されないか、
#     途中で止められるため。設定しなければ縮めない。
#   * 計画（PLAN 配列）の各行を qsub する。--submit が無ければ表示だけ。
# =============================================================================

FLOW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB="${FLOW_DIR}/job_sweep.sh"
FLOW_MAINT=${FLOW_MAINT:-}
FLOW_MAINT_MARGIN_SEC=${FLOW_MAINT_MARGIN_SEC:-1800}   # メンテナンスの30分前には終わるようにする

# $1 = 希望の walltime（HH:MM:SS）。メンテナンスにかかるなら縮めた値を表示する（警告は stderr）
flow_cap_walltime() {
    local h m s
    IFS=: read -r h m s <<< "$1"
    local want=$(( 10#${h} * 3600 + 10#${m} * 60 + 10#${s} ))
    if [ -z "${FLOW_MAINT}" ]; then
        echo "$1"
        return 0
    fi
    local left=$(( $(date -d "${FLOW_MAINT}" +%s) - $(date +%s) - FLOW_MAINT_MARGIN_SEC ))
    if [ "${left}" -le 0 ]; then
        echo "[ERROR] メンテナンス（${FLOW_MAINT}）の直前か、すでに過ぎているので投入しない" >&2
        return 1
    fi
    if [ "${want}" -gt "${left}" ]; then
        echo "[warn] walltime $1 はメンテナンスにかかるので縮める" >&2
        want=${left}
    fi
    printf "%02d:%02d:00\n" $(( want / 3600 )) $(( want % 3600 / 60 ))
}

# PLAN の各行 "<SIZE（":" で複数）> <反復（"," 区切り）> <walltime> [追加の変数（"," 区切り）]" を投入する
# $1 = 1 なら qsub する、0 なら表示だけ
flow_submit_plan() {
    local submit=$1
    if [ "${submit}" -eq 1 ]; then
        command -v qsub > /dev/null || { echo "[ERROR] qsub が見つかりません（ログインノードで実行すること）"; exit 1; }
    fi

    local njobs=0 row size reps walltime extra rep name vars wt
    local -a rep_list cmd
    for row in "${PLAN[@]}"; do
        read -r size reps walltime extra <<< "${row}"
        IFS=, read -r -a rep_list <<< "${reps}"
        wt=$(flow_cap_walltime "${walltime}")
        for rep in "${rep_list[@]}"; do
            name="tq_s${size//:/-}"
            # nb の範囲を分けたジョブは、名前に nb の下限を入れる
            if [[ "${extra}" =~ NB_MIN=([0-9]+) ]]; then
                name="${name}_nb${BASH_REMATCH[1]}"
            fi
            name="${name}_r${rep}"
            vars="SIZE=${size},REP=${rep}${extra:+,${extra}}"
            cmd=(qsub -N "${name}" -l "walltime=${wt}" -v "${vars}" "${JOB}")
            echo "${cmd[*]}"
            if [ "${submit}" -eq 1 ]; then
                "${cmd[@]}"
            fi
            njobs=$(( njobs + 1 ))
        done
    done

    echo "jobs: ${njobs}"
    if [ "${submit}" -eq 0 ]; then
        echo "（表示だけ。投入するには --submit を付ける）"
    fi
}
