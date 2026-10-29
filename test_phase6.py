"""
test_phase6.py - tests for Phase 6 (baseline, experiment runner, saved results).

  * the baseline really is SQLite-only and follows the same rules as the ledger system;
  * the event stream used for the performance runs is valid and deterministic;
  * the whole experiment script runs end to end in --quick mode (into a temporary folder, so the
    full-run logs are never overwritten) and every test case in it passes;
  * if a full run has been saved (logs/phase6_*.csv) its recorded results are internally consistent.

Run:  python test_phase6.py        (takes about 30 seconds)
Writes: logs/phase6_unit_log.csv
"""
import csv
import os
import subprocess
import sys
import tempfile
import unittest

import experiments as ex
import verifier as vf
from test_phase2 import LoggingResult

LOG_DIR = "logs"


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class UnitTests(unittest.TestCase):
    def test_01_baseline_has_no_ledger(self):
        conn = ex.new_db()
        system = ex.SQLiteBaseline(conn)
        self.assertIsNone(system.ledger)
        for op in ex.make_ops(40):
            self.assertTrue(ex.apply_op(system, op).ok)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("ledger_blocks", tables)
        self.assertEqual(system.verify("SN000001")["status"], vf.RECORD_VERIFIED)

    def test_02_blockchain_system_adds_only_the_ledger(self):
        conn = ex.new_db()
        system = ex.Blockchain(conn)
        ops = ex.make_ops(40)
        for op in ops:
            self.assertTrue(ex.apply_op(system, op).ok)
        self.assertEqual(len(system.ledger), 41)                    # genesis + 40 events
        self.assertTrue(system.ledger.verify_chain().ok)
        self.assertEqual(system.verify("SN000001")["status"], vf.RECORD_VERIFIED)

    def test_03_both_systems_apply_the_same_rules(self):
        results = []
        for cls in (ex.SQLiteBaseline, ex.Blockchain):
            conn = ex.new_db()
            system = cls(conn)
            system.register(ex.make_ops(1)[0][1], "Manufacturer_A", "2026-03-01 00:00:00")
            results.append([system.transfer("SN000001", "Rogue_Actor", "Distributor_A", "AUTH", "2026-03-02 00:00:00").code,
                            system.transfer("SN000001", "Manufacturer_A", "Pharmacy_A", "AUTH", "2026-03-02 00:00:00").code,
                            system.register(ex.make_ops(1)[0][1], "Manufacturer_A").code])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], ["UNAUTHORIZED_SENDER", "INVALID_TRANSITION", "DUPLICATE_SERIAL"])

    def test_04_event_stream_is_exact_deterministic_and_valid(self):
        for n in (1, 7, 100, 503):
            ops = ex.make_ops(n)
            self.assertEqual(len(ops), n)
            self.assertEqual(ops, ex.make_ops(n))
        ops = ex.make_ops(100)
        stamps = [o[3] if o[0] == "REGISTER" else o[5] for o in ops]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(set(stamps)), 100)

    def test_05_performance_run_returns_complete_measurements(self):
        import random
        tmp = tempfile.mkdtemp(prefix="p6test_")
        ops = ex.make_ops(60)
        for cls in (ex.SQLiteBaseline, ex.Blockchain):
            r = ex.perf_run(cls, ops, tmp, random.Random(1), 10)
            self.assertEqual((r["accepted"], r["n"]), (60, 60))
            self.assertTrue(r["chain_ok"])
            self.assertGreater(r["mean_latency_ms"], 0)
            self.assertGreater(r["throughput_eps"], 0)
            self.assertGreater(r["db_size_kb"], 0)
        self.assertGreater(r["db_size_kb"], 0)
        self.assertNotEqual(r["ledger_verify_ms"], "")              # blockchain run only

    def test_06_experiment_script_runs_end_to_end_in_quick_mode(self):
        if not (os.path.exists("pharma_traceability.db") and os.path.exists("dataset/events.csv")):
            self.skipTest("run generate_dataset.py first")
        out = tempfile.mkdtemp(prefix="p6quick_")
        p = subprocess.run([sys.executable, "experiments.py", "--quick", "--out", out],
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(p.returncode, 0, p.stdout[-1500:] + p.stderr[-1500:])
        self.assertIn("ALL TEST CASES PASSED", p.stdout)
        for name in ("phase6_test_cases.csv", "phase6_experiment_summary.csv", "phase6_metrics.csv",
                     "phase6_tamper_comparison.csv", "phase6_performance_runs.csv",
                     "phase6_performance_summary.csv", "phase6_baseline_comparison.csv",
                     "phase6_confusion_matrix.csv", "phase6_environment.txt"):
            self.assertTrue(os.path.exists(os.path.join(out, "logs", name)), name)
        for name in ("fig_latency_vs_events", "fig_throughput_vs_events", "fig_storage_overhead",
                     "fig_confusion_matrix", "fig_tamper_detection_comparison"):
            self.assertGreater(os.path.getsize(os.path.join(out, "figures", name + ".png")), 10000, name)
        rows = read(os.path.join(out, "logs", "phase6_test_cases.csv"))
        self.assertEqual([r for r in rows if r["status"] != "PASS"], [])
        self.assertFalse(os.path.samefile(out, "."))                # the full-run logs were not touched

    def test_07_saved_full_run_is_consistent(self):
        path = os.path.join(LOG_DIR, "phase6_test_cases.csv")
        if not os.path.exists(path):
            self.skipTest("no full run saved yet (python experiments.py)")
        rows = read(path)
        self.assertGreater(len(rows), 4000)
        self.assertEqual([r for r in rows if r["status"] != "PASS"], [])
        runs = read(os.path.join(LOG_DIR, "phase6_performance_runs.csv"))
        self.assertEqual({int(r["n"]) for r in runs}, {100, 500, 1000, 5000})
        self.assertTrue(all(r["accepted"] == r["n"] for r in runs))
        self.assertTrue(all(r["chain_ok"] == "True" for r in runs))
        for n in (100, 500, 1000, 5000):
            self.assertEqual(len([r for r in runs if int(r["n"]) == n and r["system"] == "baseline"]), 5)
        metrics = {m["measure"]: m for m in read(os.path.join(LOG_DIR, "phase6_metrics.csv"))}
        m = metrics["Detection rate TP/(TP+FN)"]
        self.assertEqual((m["successes"], m["n"]), ("200", "200"))
        m = metrics["False-positive rate FP/(FP+TN)"]
        self.assertEqual((m["successes"], m["n"]), ("0", "760"))


class LR6(LoggingResult):
    rows = []


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UnitTests)
    result = unittest.TextTestRunner(verbosity=1, resultclass=LR6).run(suite)
    skipped = {t._testMethodName for t, _ in result.skipped}
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(f"{LOG_DIR}/phase6_unit_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["test_case", "status"])
        w.writerows(LR6.rows)
        for name in sorted(skipped):
            w.writerow([name, "SKIPPED"])
    sys.exit(0 if result.wasSuccessful() else 1)
