#!/usr/bin/env python3
"""perf script の出力（標準入力）を、計時区間にぴったり合わせて区分けする。標準ライブラリだけ（計算機の上で動く）。

2026-11-24_epyc-libgomp での変更（前の作業 2026-11-24_task-overhead-cause の reduce_perf.py から）：
  * 区分は同じ。gompfwd（libgomp への中継）は omp_fwd として分ける
  * <prefix>_rt.csv：区間の中の試料を OpenMP のランタイムの dso ごとに（libiomp5・libgomp・gompfwd）。
    「使われているランタイムが1つだけ」の確認に使う
  * <prefix>_gompoff.csv：libgomp の試料を、ファイル上のアドレス（vaddr）ごとに（区間の中）。
    EPYC の libgomp は内部の関数のシンボルが無いので、devbox-lab で機械語と突き合わせて
    ロック待ち・バリアの待ちに分ける。perf script に --show-mmap-events を付けて渡す

11-18 の reduce_perf.py との違い：
  * 区間は推定しない。obshim が記録した omp_get_wtime の時刻（CLOCK_MONOTONIC）で切る。
    perf は `perf record -k CLOCK_MONOTONIC` で取り、時刻の軸をそろえる
  * `blas_memory_alloc`／`free` の試料を命令の位置（sym+off）ごとに数える（perf annotate の代わり）

入力：perf script --show-task-events -F comm,tid,time,ip,sym,symoff,dso
引数：--shim <obshim の jsonl>（区間）、--threads、--freq、--out <prefix>
出力：
  <prefix>_window.json  区間（stamp の組、Time との一致）、試料数
  <prefix>_cat.csv      区分ごとの試料数（区間の中：主スレッド・ワーカー、区間の外）
  <prefix>_off.csv      alloc／free の命令の位置ごとの試料数（区間の中）
  <prefix>_top.csv      区間の中の (区分, dso, sym) の上位 80
"""
import argparse
import collections
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from perfmap import Maps

ap = argparse.ArgumentParser()
ap.add_argument("--shim", required=True)
ap.add_argument("--threads", type=int, required=True)
ap.add_argument("--freq", type=int, required=True)
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
    raise SystemExit("[reduce_perf] obshim の stamp が無い")
ws, we = win["start"], win["end"]

EV = re.compile(r"(\d+\.\d+):\s+PERF_RECORD_(FORK|EXIT)\((\d+):(\d+)\):\((\d+):(\d+)\)")
# comm tid time: ip sym+off (dso)
SMP = re.compile(r"^\s*(.+?)\s+(?:\d+/)?(\d+)\s+(\d+\.\d+):\s+([0-9a-fA-F]+)\s+(.*?)\s+\((.*)\)\s*$")


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
cat = collections.defaultdict(lambda: [0, 0, 0])   # main_in, worker_in, out
off = collections.Counter()
top = collections.Counter()
rt = collections.defaultdict(lambda: [0, 0])        # dso -> main_in, worker_in
goff = collections.Counter()
maps = Maps()
n = 0
for line in sys.stdin:
    if "PERF_RECORD_MMAP" in line:
        maps.feed(line)
        continue
    m = EV.search(line)
    if m:
        if m.group(2) == "FORK" and m.group(3) != m.group(4):
            fork_parent[int(m.group(3))] += 1
        continue
    m = SMP.match(line)
    if not m:
        continue
    n += 1
    t, tid, symo, dso = float(m.group(3)), int(m.group(2)), m.group(5).strip(), m.group(6)
    sym, _, o = symo.partition("+")
    c = categorize(sym, dso)
    if main_tid is None:
        main_tid = tid                     # 最初の試料は主スレッド（スレッドの生成の前）
    inside = ws <= t <= we
    if inside:
        cat[c][0 if tid == main_tid else 1] += 1
        top[(c, os.path.basename(dso), sym)] += 1
        if c == "blas_alloc":
            off[(sym, o)] += 1
        b = os.path.basename(dso)
        if b.startswith(("libiomp5", "libgomp", "gompfwd")):
            rt[b][0 if tid == main_tid else 1] += 1
            if b.startswith("libgomp"):
                v = maps.vaddr(int(m.group(4), 16))
                goff[v[1] if v else -1] += 1
    else:
        cat[c][2] += 1

if fork_parent:
    main_tid_fork = fork_parent.most_common(1)[0][0]
    win["main_tid_check"] = (main_tid_fork == main_tid)
win.update(n_samples=n, threads=a.threads, freq=a.freq,
           samples_in=sum(v[0] + v[1] for v in cat.values()))
with open(a.out + "_window.json", "w") as f:
    json.dump(win, f, indent=1)
with open(a.out + "_cat.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["cat", "main_in", "worker_in", "out", "freq"])
    for c, v in sorted(cat.items()):
        w.writerow([c, *v, a.freq])
with open(a.out + "_off.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["sym", "off", "samples"])
    for (s, o), k in off.most_common():
        w.writerow([s, o, k])
with open(a.out + "_top.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["cat", "dso", "sym", "samples"])
    for (c, d, s), k in top.most_common(80):
        w.writerow([c, d, s, k])
with open(a.out + "_rt.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["dso", "main_in", "worker_in", "freq"])
    for d, v in sorted(rt.items()):
        w.writerow([d, *v, a.freq])
if goff:
    with open(a.out + "_gompoff.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["vaddr", "samples"])        # vaddr = -1 は読み込み位置が分からなかった試料
        for v, k in sorted(goff.items()):
            w.writerow([hex(v) if v >= 0 else -1, k])
print(f"[reduce_perf] {os.path.basename(a.out)} stamp={win['i']} len={win['len']:.4f}s Time={a.time} "
      f"rel_err={win['rel_err']} samples={n} in={win['samples_in']}")
