"""
Comprehensive test suite for lab3 hash table implementations.

DELETE THIS FILE BEFORE SUBMITTING — it is not part of the skeleton.

Goals beyond the provided test_lab3.py:
  1. Catch rare race conditions by running each configuration many times.
  2. Sweep thread counts and entry sizes (including edges and oversubscribed).
  3. Report performance statistics (min / median / max v2 speedup) instead of
     a single noisy data point.
  4. Check rubric performance tiers (strong / weak / low) so you know which
     tier the grader is likely to award.

Usage:
    python3 -m unittest test_comprehensive -v
or just:
    python3 test_comprehensive.py
"""

import os
import re
import statistics
import subprocess
import sys
import unittest

BINARY = "./hash-table-tester"
NPROC = os.cpu_count() or 1

# Regex matching the tester's full output. Captures all 7 numeric fields.
RESULT_RE = re.compile(
    r"Generation: ([\d,]+) usec\n"
    r"Hash table base: ([\d,]+) usec\n"
    r"  - ([\d,]+) missing\n"
    r"Hash table v1: ([\d,]+) usec\n"
    r"  - ([\d,]+) missing\n"
    r"Hash table v2: ([\d,]+) usec\n"
    r"  - ([\d,]+) missing\n"
)


def _to_int(s):
    return int(s.replace(",", ""))


def _run_once(threads, size):
    """Run the tester once. Returns dict of timings + missing counts."""
    out = subprocess.check_output(
        (BINARY, "-t", str(threads), "-s", str(size)),
        text=True,
    )
    m = RESULT_RE.search(out)
    if not m:
        raise AssertionError(f"Unexpected tester output:\n{out}")
    gen, base, miss0, v1, miss1, v2, miss2 = (_to_int(g) for g in m.groups())
    return {
        "gen": gen,
        "base": base,
        "v1": v1,
        "v2": v2,
        "miss_base": miss0,
        "miss_v1": miss1,
        "miss_v2": miss2,
        "raw": out,
    }


class TestBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(["make"], capture_output=True, text=True)
        cls.built = result.returncode == 0
        cls.build_output = result.stdout + result.stderr

    def test_build_succeeds(self):
        self.assertTrue(
            self.built,
            msg=f"`make` failed:\n{self.build_output}",
        )


class TestCorrectness(unittest.TestCase):
    """Run each configuration many times to flush out rare race conditions."""

    ITERATIONS = 5

    def _correctness_loop(self, threads, size):
        for i in range(self.ITERATIONS):
            r = _run_once(threads, size)
            self.assertEqual(
                r["miss_base"], 0,
                msg=f"[iter {i}] base missing {r['miss_base']} entries",
            )
            self.assertEqual(
                r["miss_v1"], 0,
                msg=f"[iter {i}] v1 missing {r['miss_v1']} entries\n{r['raw']}",
            )
            self.assertEqual(
                r["miss_v2"], 0,
                msg=f"[iter {i}] v2 missing {r['miss_v2']} entries\n{r['raw']}",
            )

    def test_t1_small(self):
        """Single thread — no concurrency, sanity baseline."""
        self._correctness_loop(threads=1, size=10_000)

    def test_t2_small(self):
        """Two threads — minimal concurrent case."""
        self._correctness_loop(threads=2, size=10_000)

    def test_t4_medium(self):
        self._correctness_loop(threads=4, size=25_000)

    def test_t8_high(self):
        """The rubric's primary test configuration."""
        self._correctness_loop(threads=8, size=50_000)

    def test_t8_secondary(self):
        self._correctness_loop(threads=8, size=40_000)

    def test_oversubscribed_t16(self):
        """More threads than cores — stress scheduler + lock interleaving."""
        self._correctness_loop(threads=16, size=10_000)

    def test_oversubscribed_t32(self):
        """Heavy oversubscription — best at finding lurking races."""
        self._correctness_loop(threads=32, size=5_000)

    def test_high_collision_density(self):
        """Many inserts per bucket → maximum chance of intra-bucket contention."""
        self._correctness_loop(threads=8, size=20_000)


class TestPerformance(unittest.TestCase):
    """
    Performance is noisy. Run a handful of times and report distribution
    statistics. Pass/fail against rubric tiers using the BEST run (the
    rubric says best-of-3 is sufficient).
    """

    PERF_RUNS = 5
    THREADS = 8
    SIZE = 50_000

    @classmethod
    def setUpClass(cls):
        cls.runs = [_run_once(cls.THREADS, cls.SIZE) for _ in range(cls.PERF_RUNS)]

    def test_no_missing_across_runs(self):
        for i, r in enumerate(self.runs):
            self.assertEqual(r["miss_v1"], 0, msg=f"run {i}: v1 missing")
            self.assertEqual(r["miss_v2"], 0, msg=f"run {i}: v2 missing")

    def test_v1_slower_than_base(self):
        """Rubric: v1 must be slower than base."""
        slower_count = sum(1 for r in self.runs if r["v1"] > r["base"])
        self.assertGreater(
            slower_count, self.PERF_RUNS // 2,
            msg=f"v1 was slower than base in only {slower_count}/{self.PERF_RUNS} runs",
        )

    def test_v2_faster_than_base(self):
        """Sanity: v2 should be faster than base in every run."""
        for i, r in enumerate(self.runs):
            self.assertLess(
                r["v2"], r["base"],
                msg=f"run {i}: v2 ({r['v2']}) not faster than base ({r['base']})",
            )

    def test_print_summary(self):
        """Not really a test — just emits a human-readable summary."""
        v1_ratios = [r["v1"] / r["base"] for r in self.runs]
        v2_speedups = [r["base"] / r["v2"] for r in self.runs]
        base_times = [r["base"] for r in self.runs]
        v2_times = [r["v2"] for r in self.runs]

        best_v2 = min(v2_times)
        worst_v2 = max(v2_times)
        med_base = statistics.median(base_times)

        strong_threshold = med_base / max(NPROC - 1, 1)
        weak_threshold = med_base / max(NPROC // 2, 1)

        print("\n" + "=" * 60)
        print(f"Performance summary ({self.PERF_RUNS} runs, "
              f"-t {self.THREADS} -s {self.SIZE}, nproc={NPROC})")
        print("=" * 60)
        for i, r in enumerate(self.runs):
            print(f"  run {i}: base={r['base']:>10,} v1={r['v1']:>10,} "
                  f"v2={r['v2']:>10,}  (v2 speedup={r['base']/r['v2']:.2f}x)")
        print("-" * 60)
        print(f"  v1/base ratio    min={min(v1_ratios):.2f}x  "
              f"median={statistics.median(v1_ratios):.2f}x  "
              f"max={max(v1_ratios):.2f}x")
        print(f"  v2 speedup       min={min(v2_speedups):.2f}x  "
              f"median={statistics.median(v2_speedups):.2f}x  "
              f"max={max(v2_speedups):.2f}x")
        print("-" * 60)
        print(f"  Rubric thresholds (median base = {int(med_base):,} usec):")
        print(f"    Strong tier (v2 <= base/{max(NPROC-1,1)}): "
              f"{int(strong_threshold):,} usec")
        print(f"    Weak tier   (v2 <= base/{max(NPROC//2,1)}): "
              f"{int(weak_threshold):,} usec")
        print(f"    Best v2 run: {best_v2:,} usec   "
              f"Worst v2 run: {worst_v2:,} usec")
        if best_v2 <= strong_threshold:
            verdict = "STRONG tier likely (best-of-3 passes base/(nproc-1))"
        elif best_v2 <= weak_threshold:
            verdict = "WEAK tier (passes base/(nproc/2) but not base/(nproc-1))"
        else:
            verdict = "LOW tier — v2 missed both high-element thresholds"
        print(f"    Verdict: {verdict}")
        print("=" * 60)


class TestValgrind(unittest.TestCase):
    """If valgrind is available, check for leaks on a small run."""

    def test_no_leaks(self):
        valgrind = subprocess.run(
            ["which", "valgrind"], capture_output=True, text=True
        )
        if valgrind.returncode != 0:
            self.skipTest("valgrind not installed")
        result = subprocess.run(
            ["valgrind", "--error-exitcode=99", "--leak-check=full",
             BINARY, "-t", "2", "-s", "500"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(
            result.returncode, 99,
            msg=f"valgrind reported errors:\n{result.stderr[-2000:]}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
