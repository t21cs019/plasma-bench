"""plan / throttle の単体テスト（標準ライブラリのみ、実行ファイル不要）。

実行:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import csv
import os
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from plasma_perf.core.throttle import ThrottleMonitor, read_counts  # noqa: E402
from plasma_perf.plan import coverage, manifest  # noqa: E402
from plasma_perf.plan.manifest import BenchRow, Manifest, TuneRow  # noqa: E402


class TestManifestRoundTrip(unittest.TestCase):
    def _sample(self):
        m = Manifest()
        m.ssrfb.append(BenchRow("ssrfb", 4096, 1, 32, 512, 8, 4, 5, n_it=50, note="手動"))
        m.tileqr.append(BenchRow("tileqr", 2048, 12, 32, 512, 8, 4, 5))
        m.tuning.append(TuneRow("select", 4096, 12, 25, 10, note="skip"))
        return m

    def test_render_parse_roundtrip(self):
        m = self._sample()
        parsed = manifest.parse(manifest.render(m))
        self.assertEqual(len(parsed.ssrfb), 1)
        s = parsed.ssrfb[0]
        self.assertEqual((s.size, s.threads, s.n_it, s.nb_min, s.nb_max, s.trials),
                         (4096, 1, 50, 32, 512, 5))
        self.assertEqual(s.note, "手動")
        t = parsed.tileqr[0]
        self.assertEqual((t.size, t.threads, t.nb_max), (2048, 12, 512))
        self.assertIsNone(t.n_it)
        u = parsed.tuning[0]
        self.assertEqual((u.mode, u.size, u.n_trials, u.counts, u.note),
                         ("select", 4096, 25, 10, "skip"))

    def test_loose_row_is_parsed(self):
        # 桁が揃っていない手書き行も読める
        text = ("## ssrfb\n"
                "| size | threads | n_it | nb | ib_min | step | trials | status | coverage | note |\n"
                "|---|---|---|---|---|---|---|---|---|---|\n"
                "|4096|1|50|32-512|8|4|5| | | |\n"
                "## tileqr\n## tuning\n")
        m = manifest.parse(text)
        self.assertEqual(len(m.ssrfb), 1)
        self.assertEqual(m.ssrfb[0].nb_max, 512)


class TestBenchCoverage(unittest.TestCase):
    def test_status_and_resume(self):
        row = BenchRow("ssrfb", 4096, 1, 32, 40, 8, 4, 1, n_it=50)
        # 全点: nb=32(ib 8,12,16), nb=36(8,12,16), nb=40(8,12,16,20)
        # nb=32,36 は充足、nb=40 は ib=20 が欠け → partial, resume=40
        covered = set()
        for nb in (32, 36):
            for ib in range(8, nb // 2 + 1, 4):
                covered.add((nb, ib))
        for ib in (8, 12, 16):  # nb=40 の一部だけ
            covered.add((40, ib))
        index = {("ssrfb", 4096, 1): covered}
        resume = coverage.bench_status(row, index)
        self.assertEqual(row.status, "partial")
        self.assertEqual(resume, 40)
        self.assertTrue(row.coverage.endswith("/10"))

    def test_done(self):
        row = BenchRow("tileqr", 4096, 1, 32, 40, 8, 4, 1)
        covered = set()
        for nb in range(32, 41, 4):
            for ib in range(8, nb // 2 + 1, 4):
                covered.add((nb, ib))
        index = {("tileqr", 4096, 1): covered}
        resume = coverage.bench_status(row, index)
        self.assertEqual(row.status, "done")
        self.assertIsNone(resume)

    def test_missing(self):
        row = BenchRow("ssrfb", 9999, 1, 32, 40, 8, 4, 1, n_it=50)
        resume = coverage.bench_status(row, {})
        self.assertEqual(row.status, "missing")
        self.assertEqual(resume, 32)


class TestScanBenchPoints(unittest.TestCase):
    def test_scan_reads_valid_points(self):
        host = socket.gethostname()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, f"{host}_ssrfb_size4096_nb32-64_t1_x.csv")
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["threads", "size", "nb", "ib", "Time_sec", "GFlops"])
                w.writerow([1, 4096, 32, 8, 0.001, 20.0])     # valid
                w.writerow([1, 4096, 32, 12, -1, "nan"])       # 欠測（除外）
            index = coverage.scan_bench_points(d, host=host)
            self.assertIn(("ssrfb", 4096, 1), index)
            self.assertEqual(index[("ssrfb", 4096, 1)], {(32, 8)})

    def test_other_host_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "otherhost_ssrfb_size4096_nb32-64_t1_x.csv")
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["threads", "size", "nb", "ib", "Time_sec", "GFlops"])
                w.writerow([1, 4096, 32, 8, 0.001, 20.0])
            index = coverage.scan_bench_points(d, host="thishost")
            self.assertEqual(index, {})


class TestThrottle(unittest.TestCase):
    def test_read_counts_type(self):
        self.assertIsInstance(read_counts(), dict)

    def test_disabled_monitor_is_noop(self):
        mon = ThrottleMonitor(enabled=False)
        mon.start()
        self.assertFalse(mon.check("nb=32"))
        mon.summary()  # 例外を出さない


if __name__ == "__main__":
    unittest.main()
