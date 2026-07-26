"""core / runner の単体テスト（標準ライブラリのみ、実行ファイル不要）。

実行:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import plasma_perf.benchmarks.ssrfb.runner as ssrfb_runner  # noqa: E402
import plasma_perf.benchmarks.tileqr.runner as tileqr_runner  # noqa: E402
from plasma_perf.benchmarks.base import Benchmark, PointResult  # noqa: E402
from plasma_perf.benchmarks.ssrfb.runner import (  # noqa: E402
    SSRFBBenchmark, flops_ssrfb, parse_time,
)
from plasma_perf.benchmarks.tileqr.runner import TileQRBenchmark, parse_gflops  # noqa: E402
from plasma_perf.core.csv_io import build_output_path, failure_reason  # noqa: E402
from plasma_perf.core.process import ProcResult  # noqa: E402
from plasma_perf.core.sweep import count_points, csv_fieldnames, run_sweep  # noqa: E402
from plasma_perf.core.timeout import estimate_timeout_sec  # noqa: E402


class TestParsers(unittest.TestCase):
    def test_parse_gflops(self):
        # plasmatest の pass 行から3番目(GFlops)を取る
        out = "  routine  ...\n  pass   1.2e-15   3.4e-16   28.577\n"
        self.assertAlmostEqual(parse_gflops(out), 28.577)

    def test_parse_gflops_fail(self):
        self.assertIsNone(parse_gflops("FAILED: something went wrong"))

    def test_parse_time(self):
        self.assertAlmostEqual(parse_time("256, 64, 0.00234831"), 0.00234831)

    def test_parse_time_fail(self):
        self.assertIsNone(parse_time("no numbers here"))


class TestFlopsTimeout(unittest.TestCase):
    def test_flops_ssrfb(self):
        self.assertEqual(flops_ssrfb(256, 64), 4 * 256 ** 3)

    def test_timeout_floor(self):
        # 小さい nb では min_timeout(60) が効く
        self.assertEqual(estimate_timeout_sec(flops_ssrfb, 4, 2, n_it=1), 60.0)

    def test_timeout_scales_with_n_it(self):
        t1 = estimate_timeout_sec(flops_ssrfb, 512, 64, n_it=1)
        t2 = estimate_timeout_sec(flops_ssrfb, 512, 64, n_it=50000)
        self.assertGreater(t2, t1)


class TestCsvIo(unittest.TestCase):
    def test_build_output_path_tileqr(self):
        p = build_output_path("/r", size=4096, nb_min=32, nb_max=512, trials=5, host="h")
        self.assertEqual(os.path.basename(p).split("_")[0], "h")
        self.assertIn("size4096", p)
        self.assertIn("nb32-512", p)
        self.assertIn("t5", p)
        self.assertNotIn("_ssrfb_", p)
        self.assertNotIn("_th", p)

    def test_build_output_path_ssrfb_with_threads(self):
        p = build_output_path("/r", size=4096, nb_min=32, nb_max=512, trials=5,
                              tag="ssrfb", threads=4, host="h")
        self.assertIn("_ssrfb_", "_" + os.path.basename(p))
        self.assertIn("th4", p)

    def test_failure_reason(self):
        self.assertEqual(failure_reason(ProcResult(None, "", "", timed_out=True)), "timeout")
        self.assertEqual(failure_reason(ProcResult(2, "", "")), "rc=2")
        self.assertEqual(failure_reason(ProcResult(0, "", "")), "parse-fail")


class TestCountPoints(unittest.TestCase):
    def test_count_points(self):
        # nb も step 刻み、各 nb で ib=ib_min..nb//2 を step 刻みで数える
        expected = sum(len(range(8, nb // 2 + 1, 4)) for nb in range(32, 64 + 1, 4))
        self.assertEqual(count_points(32, 64, 8, 4), expected)


class _FakeBenchmark(Benchmark):
    """subprocess を使わないダミー。ib==8 のときだけ欠測を返す。"""
    name = "fake"
    tag = ""
    value_fields = ["GFlops"]

    def make_env(self, threads):
        return {}

    def run_point(self, nb, ib, *, threads, size, env, timeout, wrapper):
        if ib == 8:
            return PointResult({"GFlops": float("nan")}, ok=False,
                               proc=ProcResult(1, "", "boom"))
        return PointResult({"GFlops": float(nb + ib)}, ok=True,
                           proc=ProcResult(0, "ok", ""))


class TestRunSweep(unittest.TestCase):
    def test_sweep_writes_csv_and_counts_missing(self):
        bench = _FakeBenchmark()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out.csv")
            missing = run_sweep(
                bench, threads=1, size=4096,
                nb_min=32, nb_max=40, ib_min=8, step=4, trials=1,
                output_csv=out, log_dir=os.path.join(d, "logs"),
                save_logs="none",
            )
            self.assertTrue(os.path.exists(out))
            with open(out) as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            # ヘッダ + データ行
            self.assertEqual(lines[0], ",".join(csv_fieldnames(bench)))
            data = lines[1:]
            self.assertEqual(len(data), count_points(32, 40, 8, 4))
            # ib==8 の点はすべて欠測
            self.assertEqual(missing, sum(1 for nb in range(32, 41, 4)
                                          for ib in range(8, nb // 2 + 1, 4) if ib == 8))


class TestRunners(unittest.TestCase):
    """run_process をモックして、コマンド組み立てと欠測値の規約を検証する。"""

    def test_ssrfb_command_carries_matsize_and_nit(self):
        captured = {}

        def fake_run(command, env=None, timeout=None, wrapper=None):
            captured["cmd"] = list(command)
            return ProcResult(0, "128, 16, 0.001", "")

        orig = ssrfb_runner.run_process
        ssrfb_runner.run_process = fake_run
        try:
            bench = SSRFBBenchmark(exe="/bin/true", n_it=123)
            res = bench.run_point(128, 16, threads=1, size=2048,
                                  env={}, timeout=None, wrapper=None)
        finally:
            ssrfb_runner.run_process = orig

        # NoFlush [NB] [IB] [MAT_SIZE] [N_IT]
        self.assertEqual(captured["cmd"], ["/bin/true", "128", "16", "2048", "123"])
        self.assertTrue(res.ok)
        self.assertAlmostEqual(res.values["GFlops"], (4 * 128 ** 3) / 0.001 / 1e9)

    def test_ssrfb_missing_sentinels(self):
        orig = ssrfb_runner.run_process
        ssrfb_runner.run_process = lambda *a, **k: ProcResult(2, "", "boom")
        try:
            bench = SSRFBBenchmark(exe="/bin/true")
            res = bench.run_point(64, 8, threads=1, size=4096,
                                  env={}, timeout=None, wrapper=None)
        finally:
            ssrfb_runner.run_process = orig
        self.assertFalse(res.ok)
        self.assertEqual(res.values["Time_sec"], -1.0)

    def test_tileqr_missing_is_zero(self):
        # 旧 tileQR_bench 互換: 欠測は 0.0 で記録する
        orig = tileqr_runner.run_process
        tileqr_runner.run_process = lambda *a, **k: ProcResult(1, "", "boom")
        try:
            bench = TileQRBenchmark(exe="/bin/true")
            res = bench.run_point(64, 8, threads=1, size=256,
                                  env={}, timeout=None, wrapper=None)
        finally:
            tileqr_runner.run_process = orig
        self.assertFalse(res.ok)
        self.assertEqual(res.values["GFlops"], 0.0)


if __name__ == "__main__":
    unittest.main()
