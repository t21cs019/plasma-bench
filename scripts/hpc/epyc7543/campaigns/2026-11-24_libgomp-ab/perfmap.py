"""perf script の --show-mmap-events の行から、共有ライブラリの読み込み位置を覚え、試料の ip をファイル上のアドレス（vaddr）に直す。
標準ライブラリだけ（計算機の上で動く）。reduce_perf.py・reduce_perfg.py から使う。

EPYC 7543 の libgomp（Ubuntu の libgomp1）は内部の関数（gomp_mutex_lock_slow など）のシンボルが削られていて、perf では
[unknown] になる。そこで ip をファイル上のアドレスに直して数えておき、devbox-lab で機械語（preflight の libgomp.dis）と突き合わせる。
"""
import os
import re
import subprocess

# 例：plasmatest 1234 123.456: PERF_RECORD_MMAP2 1234/1234: [0x7f0000001000(0x2a000) @ 0x5000 08:01 1234 0]: r-xp /usr/lib/x86_64-linux-gnu/libgomp.so.1.0.0
MMAP = re.compile(r"PERF_RECORD_MMAP2?\s+(\d+)/\d+:\s+\[0x([0-9a-fA-F]+)\(0x([0-9a-fA-F]+)\)\s+@\s+(0x[0-9a-fA-F]+|\d+)[^\]]*\]:\s+(\S+)\s+(\S+)")


class Maps:
    def __init__(self, want=("libgomp", "libiomp5", "gompfwd")):
        self.want = want
        self.maps = []          # (start, end, pgoff, path)
        self.loads = {}         # path -> [(p_offset, p_vaddr, p_filesz)]

    def feed(self, line):
        """mmap の行なら覚えて True"""
        m = MMAP.search(line)
        if not m:
            return False
        prot, path = m.group(5), m.group(6)
        if "x" not in prot or not any(w in os.path.basename(path) for w in self.want):
            return True
        start, size = int(m.group(2), 16), int(m.group(3), 16)
        pg = m.group(4)
        pgoff = int(pg, 16) if pg.startswith("0x") else int(pg)
        self.maps.append((start, start + size, pgoff, path))
        return True

    def _loads(self, path):
        if path not in self.loads:
            segs = []
            try:
                out = subprocess.run(["readelf", "-lW", path], capture_output=True, text=True).stdout
                for l in out.splitlines():
                    f = l.split()
                    if f and f[0] == "LOAD":
                        segs.append((int(f[1], 16), int(f[2], 16), int(f[4], 16)))
            except OSError:
                pass
            self.loads[path] = segs
        return self.loads[path]

    def vaddr(self, ip):
        """ip → (ライブラリの basename, ファイル上の vaddr)。当てはまらなければ None"""
        for s, e, pgoff, path in self.maps:
            if s <= ip < e:
                off = ip - s + pgoff
                for p_off, p_va, fsz in self._loads(path):
                    if p_off <= off < p_off + fsz:
                        return os.path.basename(path), off - p_off + p_va
                return os.path.basename(path), off
        return None
