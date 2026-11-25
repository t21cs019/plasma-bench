#!/usr/bin/env python3
"""perf script の出力（標準入力。イベントが複数）を、計時区間にぴったり合わせて、イベント・区分・関数ごとに数える。
標準ライブラリだけ（計算機の上で動く）。

2026-11-24_libgomp-ab の reduce_perf.py との違い：
  * 1 回の perf record に複数のイベント（組 A：task-clock・cycles・instructions、組 B：cycles と取り寄せ先 4 つ）がある。
    どの試料も period（周期）を持つので、数は「区間の中の試料の period の合計」で出す（task-clock は ns、ほかは回数）
  * 区分（categorize）は前回と同じ。計算 ＝ blas_compute ＋ core_blas
  * 区間は前回と同じく、obshim が記録した omp_get_wtime の時刻（CLOCK_MONOTONIC）の組のうち、差が Time に最も近いもの

入力：perf script --show-task-events -F comm,tid,time,period,event,ip,sym,symoff,dso
引数：--shim <obshim の jsonl>、--threads、--time <plasmatest の Time>、--out <prefix>
出力：
  <prefix>_window.json  区間（stamp の組、Time との一致）、イベントごとの試料数（全体・区間の中）、読めなかった行の数
  <prefix>_events.csv   区間の中の (event, cat, dso, sym) ごとの period の合計（主スレッド・ワーカー）と試料数。
                        区間の外はイベントごとに cat=_out の1行
"""
import argparse
import collections
import csv
import json
import os
import re
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--shim", required=True)
ap.add_argument("--threads", type=int, required=True)
ap.add_argument("--time", type=float, default=0.0, help="plasmatest の Time（stamp の組を確かめる）")
ap.add_argument("--out", required=True)
a = ap.parse_args()


def shim_window(path, time_sec):
    """stamp の組のうち、差が Time に最も近い連続した組（i, i+1）を返す。返り値は秒（CLOCK_MONOTONIC）"""
    st = [json.loads(l) for l in open(path) if '"stamp"' in l]
    st = [s for s in st if s.get("type") == "stamp"]
    best = None
    for i in range(len(st) - 1):
        d = (st[i + 1]["mono_ns"] - st[i]["mono_ns"]) * 1e-9
        err = abs(d - time_sec) if time_sec > 0 else 0
        if best is None or err < best[0]:
            best = (err, i, d)
        if time_sec <= 0:
            break
    if best is None:
        return None
    _, i, d = best
    return dict(i=i, start=st[i]["mono_ns"] * 1e-9, end=st[i + 1]["mono_ns"] * 1e-9, len=d,
                time_sec=time_sec, rel_err=(d - time_sec) / time_sec if time_sec > 0 else None)


win = shim_window(a.shim, a.time)
if not win:
    raise SystemExit("[reduce_perfev] obshim の stamp が無い")
ws, we = win["start"], win["end"]

EV = re.compile(r"(\d+\.\d+):\s+PERF_RECORD_(FORK|EXIT)\((\d+):(\d+)\):\((\d+):(\d+)\)")
# comm tid time: period event: ip sym+off (dso)
SMP = re.compile(r"^\s*(.+?)\s+(?:\d+/)?(\d+)\s+(\d+\.\d+):\s+(\d+)\s+(\S+):\s+([0-9a-fA-F]+)\s+(.*?)\s+\((.*)\)\s*$")


def evname(e):
    """perf の表示（cycles／cycles/period=1000003/／cpu/event=..,name=fill_x/／fill_x）を短い名前に"""
    m = re.search(r"name=([^,/]+)", e)
    if m:
        return m.group(1)
    return e.split("/")[0].split(":")[0]


def categorize(sym, dso):
    base = os.path.basename(dso)
    if dso.startswith("[kernel") or dso == "[unknown]":
        return "kernel"           # 11-18 と同じく、[unknown] はカーネル等として扱う
    if base.startswith("obshim"):
        return "shim"
    if base.startswith("gompfwd"):
        return "omp_fwd"
    if base.startswith("libiomp5") or base.startswith("libgomp"):
        if re.search(r"task|steal|execute|alloc|queue|deque|dep_|hash|bootstrap|taskdata", sym, re.I):
            return "omp_task"
        if re.search(r"wait|yield|spin|barrier|sleep|suspend|pause|monitor|relax|flag|futex|do_spin", sym, re.I):
            return "omp_wait"
        return "omp_other"
    if base.startswith("libopenblas") or base.startswith("libmkl") or ("blas" in base and "plasma" not in base):
        if re.match(r"blas_memory_(alloc|free)", sym):
            return "blas_alloc"
        return "blas_compute"
    if base.startswith("libplasma_core_blas"):
        return "core_blas"
    if base.startswith("libplasma"):
        return "plasma"
    if re.search(r"^libc\.|^libc-|libpthread|ld-linux|ld\.so", base):
        return "libc"
    return "other_user"


main_tid = None
fork_parent = collections.Counter()
acc = collections.defaultdict(lambda: [0, 0, 0])     # (ev, cat, dso, sym) -> main_period, worker_period, samples
out = collections.defaultdict(lambda: [0, 0])        # ev -> period, samples（区間の外）
n_ev = collections.Counter()
n_in = collections.Counter()
bad = 0
for line in sys.stdin:
    if "PERF_RECORD_" in line:
        m = EV.search(line)
        if m and m.group(2) == "FORK" and m.group(3) != m.group(4):
            fork_parent[int(m.group(3))] += 1
        continue
    m = SMP.match(line)
    if not m:
        if line.strip():
            bad += 1
        continue
    t, tid, period, ev = float(m.group(3)), int(m.group(2)), int(m.group(4)), evname(m.group(5))
    symo, dso = m.group(7).strip(), m.group(8)
    sym = symo.partition("+")[0]
    if main_tid is None:
        main_tid = tid                     # 最初の試料は主スレッド（スレッドの生成の前）
    n_ev[ev] += 1
    if ws <= t <= we:
        n_in[ev] += 1
        r = acc[(ev, categorize(sym, dso), os.path.basename(dso), sym)]
        r[0 if tid == main_tid else 1] += period
        r[2] += 1
    else:
        out[ev][0] += period
        out[ev][1] += 1

if fork_parent:
    win["main_tid_check"] = (fork_parent.most_common(1)[0][0] == main_tid)
win.update(threads=a.threads, samples=dict(n_ev), samples_in=dict(n_in), unparsed_lines=bad)
with open(a.out + "_window.json", "w") as f:
    json.dump(win, f, indent=1)
with open(a.out + "_events.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["event", "cat", "dso", "sym", "main_in", "worker_in", "samples_in"])
    for k, v in sorted(acc.items(), key=lambda kv: (kv[0][0], -(kv[1][0] + kv[1][1]))):
        w.writerow([*k, *v])
    for ev, v in sorted(out.items()):
        w.writerow([ev, "_out", "", "", "", v[0], v[1]])
print(f"[reduce_perfev] {os.path.basename(a.out)} stamp={win['i']} len={win['len']:.4f}s Time={a.time} "
      f"rel_err={win['rel_err']} in={dict(n_in)} unparsed={bad}")
if not n_in:
    sys.exit(2)
