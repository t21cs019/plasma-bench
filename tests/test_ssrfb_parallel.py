"""ssrfb_parallel の単体テスト（標準ライブラリのみ、NoFlush 不要）。

実行:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from plasma_perf.benchmarks.ssrfb_parallel import runner  # noqa: E402

# 2ソケット x 2コア x SMT2。CPU 番号がソケット間で交互に振られている機種を想定。
_LSCPU = """\
# The following is the parsable format
# CPU,Core,Socket,Node
0,0,0,0
1,2,1,1
2,1,0,0
3,3,1,1
4,0,0,0
5,2,1,1
6,1,0,0
7,3,1,1
"""


class TestCpuSelection(unittest.TestCase):
    def test_socket0_one_cpu_per_core(self):
        rows = runner.parse_lscpu(_LSCPU)
        self.assertEqual(runner.socket_cpus(rows, 0), [0, 2])
        self.assertEqual(runner.socket_cpus(rows, 1), [1, 3])

    def test_respects_affinity(self):
        rows = runner.parse_lscpu(_LSCPU)
        # CPU 0 が使えないなら、コア0は SMT 兄弟の CPU 4 で代替する
        self.assertEqual(runner.socket_cpus(rows, 0, allowed=[2, 4, 6]), [2, 4])

    def test_parse_cpu_list(self):
        self.assertEqual(runner.parse_cpu_list("0-3,8,10-11"), [0, 1, 2, 3, 8, 10, 11])


class TestChooseNIt(unittest.TestCase):
    def test_scales_to_target(self):
        self.assertEqual(runner.choose_n_it(0.001, 2.0, 50), 2000)

    def test_floor(self):
        self.assertEqual(runner.choose_n_it(1.0, 2.0, 50), 50)

    def test_unknown(self):
        self.assertEqual(runner.choose_n_it(None, 2.0, 50), 50)


@unittest.skipUnless(sys.platform.startswith("linux"), "taskset が必要")
class TestRunParallelPoint(unittest.TestCase):
    def _fake_exe(self, body):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "NoFlush")
        with open(path, "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def test_all_cores_report(self):
        exe = self._fake_exe('echo "$1, $2, 0.002"\n')
        cpus = sorted(os.sched_getaffinity(0))[:2]
        res = runner.run_parallel_point(exe, 64, 16, size=256, n_it=50, cpus=cpus,
                                        env=os.environ.copy(), timeout=30)
        self.assertEqual([r.cpu for r in res], cpus)
        self.assertTrue(all(abs(r.time_sec - 0.002) < 1e-12 for r in res))

    def test_timeout_kills_all(self):
        exe = self._fake_exe("exec sleep 30\n")
        cpus = sorted(os.sched_getaffinity(0))[:2]
        res = runner.run_parallel_point(exe, 64, 16, size=256, n_it=50, cpus=cpus,
                                        env=os.environ.copy(), timeout=0.5)
        self.assertTrue(all(r.timed_out and r.time_sec is None for r in res))


if __name__ == "__main__":
    unittest.main()
