#!/usr/bin/env python3
"""perf record --call-graph の perf script（標準入力）から、計時区間の中で libgomp にいた試料の呼び出しの連なりを数える。
標準ライブラリだけ（計算機の上で動く）。

目的：不老・弐で 8 割以上を占めた gomp_mutex_lock_slow が「どのロックか」を、呼び出し元（GOMP_task か、
バリアでタスクを取りに行く処理か、GOMP_taskwait か…）で確かめる。EPYC の libgomp は内部の関数のシンボルが
無いので、libgomp の中の枠はファイル上のアドレス（vaddr）で残し、devbox-lab で機械語と突き合わせる。

入力：perf script --show-mmap-events --show-task-events -F comm,tid,time,ip,sym,symoff,dso
  （callchain があるとき、試料は「見出しの行」＋「枠の行（字下げ）」＋空行）
引数：--shim <obshim の jsonl>、--time <Time>、--out <prefix>、--depth（残す枠の数。既定 6）
出力：
  <prefix>_chains.csv   (葉の vaddr, 枠1..depth) ごとの試料数（区間の中、葉が libgomp のもの）
  <prefix>_gwindow.json 区間、試料数
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
ap.add_argument("--time", type=float, default=0.0)
ap.add_argument("--out", required=True)
ap.add_argument("--depth", type=int, default=6)
a = ap.parse_args()

st = [json.loads(l) for l in open(a.shim) if '"stamp"' in l]
st = [s for s in st if s.get("type") == "stamp"]
best = None
for i in range(len(st) - 1):
    d = (st[i + 1]["mono_ns"] - st[i]["mono_ns"]) * 1e-9
    err = abs(d - a.time) if a.time > 0 else 0
    if best is None or err < best[0]:
        best = (err, i, d)
if best is None:
    raise SystemExit("[reduce_perfg] obshim の stamp が無い")
_, wi, wl = best
ws, we = st[wi]["mono_ns"] * 1e-9, st[wi + 1]["mono_ns"] * 1e-9

HEAD = re.compile(r"^\s*(\S.*?)\s+(?:\d+/)?(\d+)\s+(\d+\.\d+):\s*(.*)$")
FRAME = re.compile(r"^\s+([0-9a-fA-F]+)\s+(.*?)\s+\((.*)\)\s*$")
maps = Maps()
chains = collections.Counter()
n_all = n_in = n_gomp = 0


def label(ip, sym, dso):
    b = os.path.basename(dso)
    if b.startswith(("libgomp", "gompfwd", "libiomp5")):
        v = maps.vaddr(ip)
        if v:
            return f"{b}:{v[1]:#x}"
    return f"{b}:{sym}"


def flush(t, frames):
    global n_in, n_gomp
    if t is None or not frames:
        return
    if not (ws <= t <= we):
        return
    n_in += 1
    ip, sym, dso = frames[0]
    if not os.path.basename(dso).startswith("libgomp"):
        return
    n_gomp += 1
    chains[tuple(label(*f) for f in frames[: a.depth])] += 1


cur_t, frames = None, []
for line in sys.stdin:
    if "PERF_RECORD_MMAP" in line:
        maps.feed(line)
        continue
    if "PERF_RECORD_" in line:
        continue
    fm = FRAME.match(line)
    if fm and cur_t is not None:
        sym = fm.group(2).strip().partition("+")[0]
        frames.append((int(fm.group(1), 16), sym, fm.group(3)))
        continue
    hm = HEAD.match(line)
    if hm:
        flush(cur_t, frames)
        n_all += 1
        cur_t, frames = float(hm.group(3)), []
        continue
    if not line.strip():
        flush(cur_t, frames)
        cur_t, frames = None, []
flush(cur_t, frames)

with open(a.out + "_chains.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["samples"] + [f"f{i}" for i in range(a.depth)])
    for ch, k in chains.most_common(400):
        w.writerow([k, *ch, *[""] * (a.depth - len(ch))])
json.dump(dict(start=ws, end=we, len=wl, time_sec=a.time, n_samples=n_all, samples_in=n_in, libgomp_leaf_in=n_gomp),
          open(a.out + "_gwindow.json", "w"), indent=1)
print(f"[reduce_perfg] {os.path.basename(a.out)} len={wl:.4f}s samples={n_all} in={n_in} libgomp_leaf={n_gomp} chains={len(chains)}")
