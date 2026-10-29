"""
test_phase2.py - tests for Phase 2 (registration + supply-chain tracking).

Part A: unit tests, one per rule, on a small in-memory database.
Part B: replay of the 1,000 synthetic test packages (dataset/*.csv) through
        register_product() / transfer_product() into a CLEAN database
        (pharma_replay.db), comparing actual vs expected outcome per scenario.

Run:  python test_phase2.py
Writes: logs/phase2_unit_log.csv, logs/phase2_replay_log.csv   (kept as test evidence)
"""
import csv
import os
import sys
import unittest
from collections import Counter, defaultdict

from database import create_schema, get_connection, reset_database
from generate_dataset import PARTICIPANTS
import supply_chain as sc

LOG_DIR = "logs"
DATA_DIR = "dataset"

GOOD = dict(serial_number="SN000001", drug_id="DRG001", product_name="Sample Antibiotic A",
            batch_number="BATCH2026A", manufacturer="Manufacturer_A",
            manufacturing_date="2026-01-10", expiry_date="2028-01-10")


def fresh_db():
    conn = get_connection(":memory:")
    create_schema(conn)
    conn.executemany("INSERT INTO participants VALUES (?,?,?)", PARTICIPANTS)
    conn.commit()
    return conn


class FakeLedger:
    def __init__(self):
        self.events = []

    def add_event(self, e):
        self.events.append(e)


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()

    def reg(self, **kw):
        p = dict(GOOD)
        p.update(kw)
        return sc.register_product(self.db, p, "Manufacturer_A", timestamp="2026-02-01 10:00:00")

    def full_chain(self):
        self.assertTrue(self.reg().ok)
        steps = [("Manufacturer_A", "Distributor_A", "2026-02-02 10:00:00"),
                 ("Distributor_A", "Wholesaler_A", "2026-02-03 10:00:00"),
                 ("Wholesaler_A", "Pharmacy_A", "2026-02-04 10:00:00")]
        for s, r, t in steps:
            res = sc.transfer_product(self.db, "SN000001", s, r, f"AUTH-{s}-SN000001", t)
            self.assertTrue(res.ok, res)

    # ---- registration ----
    def test_01_valid_registration(self):
        r = self.reg()
        self.assertTrue(r.ok)
        p = sc.get_product(self.db, "SN000001")
        self.assertEqual((p["current_owner"], p["current_status"]), ("Manufacturer_A", "Registered"))
        h = sc.get_history(self.db, "SN000001")
        self.assertEqual([e["event_type"] for e in h], ["REGISTER"])

    def test_02_duplicate_serial_rejected(self):
        self.reg()
        r = self.reg(manufacturer="Manufacturer_B")
        self.assertEqual((r.ok, r.code), (False, sc.DUPLICATE_SERIAL))
        self.assertEqual(len(sc.get_history(self.db, "SN000001")), 1)

    def test_03_missing_field_rejected(self):
        r = self.reg(batch_number="")
        self.assertEqual((r.ok, r.code), (False, sc.MISSING_FIELD))
        self.assertIsNone(sc.get_product(self.db, "SN000001"))

    def test_04_unauthorized_registrant_rejected(self):
        for who in ("Distributor_A", "Rogue_Actor", "Nobody"):
            r = sc.register_product(self.db, dict(GOOD), who)
            self.assertEqual((r.ok, r.code), (False, sc.UNAUTHORIZED_REGISTRANT), who)

    def test_05_invalid_dates_rejected(self):
        self.assertEqual(self.reg(expiry_date="2025-01-01").code, sc.INVALID_DATE)
        self.assertEqual(self.reg(expiry_date="not-a-date").code, sc.INVALID_DATE)

    # ---- transfers ----
    def test_06_full_chain_accepted(self):
        self.full_chain()
        p = sc.get_product(self.db, "SN000001")
        self.assertEqual((p["current_owner"], p["current_status"]), ("Pharmacy_A", "At pharmacy"))
        self.assertEqual(len(sc.get_history(self.db, "SN000001")), 4)

    def test_07_unknown_product_rejected(self):
        r = sc.transfer_product(self.db, "SN999999", "Distributor_A", "Wholesaler_A", "AUTH-x")
        self.assertEqual((r.ok, r.code), (False, sc.UNKNOWN_PRODUCT))

    def test_08_unauthorized_sender_rejected(self):
        self.reg()
        r = sc.transfer_product(self.db, "SN000001", "Rogue_Actor", "Pharmacy_A", "AUTH-x",
                                "2026-02-02 10:00:00")
        self.assertEqual((r.ok, r.code), (False, sc.UNAUTHORIZED_SENDER))

    def test_09_unauthorized_receiver_rejected(self):
        self.reg()
        r = sc.transfer_product(self.db, "SN000001", "Manufacturer_A", "Rogue_Actor", "AUTH-x",
                                "2026-02-02 10:00:00")
        self.assertEqual((r.ok, r.code), (False, sc.UNAUTHORIZED_RECEIVER))

    def test_10_missing_evidence_rejected(self):
        self.reg()
        for ev in ("", None, "NONE"):
            r = sc.transfer_product(self.db, "SN000001", "Manufacturer_A", "Distributor_A", ev,
                                    "2026-02-02 10:00:00")
            self.assertEqual((r.ok, r.code), (False, sc.MISSING_EVIDENCE), ev)

    def test_11_wrong_owner_rejected(self):
        self.reg()
        r = sc.transfer_product(self.db, "SN000001", "Distributor_A", "Wholesaler_A", "AUTH-x",
                                "2026-02-02 10:00:00")
        self.assertEqual((r.ok, r.code), (False, sc.INVALID_OWNERSHIP))

    def test_12_skipped_stage_rejected(self):
        self.reg()
        sc.transfer_product(self.db, "SN000001", "Manufacturer_A", "Distributor_A", "AUTH-x",
                            "2026-02-02 10:00:00")
        r = sc.transfer_product(self.db, "SN000001", "Distributor_A", "Pharmacy_A", "AUTH-x",
                                "2026-02-03 10:00:00")
        self.assertEqual((r.ok, r.code), (False, sc.INVALID_TRANSITION))

    def test_13_backward_transfer_rejected(self):
        self.reg()
        sc.transfer_product(self.db, "SN000001", "Manufacturer_A", "Distributor_A", "AUTH-x",
                            "2026-02-02 10:00:00")
        sc.transfer_product(self.db, "SN000001", "Distributor_A", "Wholesaler_A", "AUTH-x",
                            "2026-02-03 10:00:00")
        r = sc.transfer_product(self.db, "SN000001", "Wholesaler_A", "Distributor_A", "AUTH-x",
                                "2026-02-04 10:00:00")
        self.assertEqual((r.ok, r.code), (False, sc.INVALID_TRANSITION))

    def test_14_timestamp_not_after_previous_rejected(self):
        self.reg()
        r = sc.transfer_product(self.db, "SN000001", "Manufacturer_A", "Distributor_A", "AUTH-x",
                                "2026-01-01 00:00:00")
        self.assertEqual((r.ok, r.code), (False, sc.INVALID_TIMESTAMP))

    def test_15_rejection_changes_nothing(self):
        self.reg()
        before_hist = sc.get_history(self.db, "SN000001")
        before_prod = dict(sc.get_product(self.db, "SN000001"))
        sc.transfer_product(self.db, "SN000001", "Rogue_Actor", "Pharmacy_A", "NONE",
                            "2026-02-02 10:00:00")
        sc.transfer_product(self.db, "SN000001", "Distributor_A", "Wholesaler_A", "AUTH-x",
                            "2026-02-02 10:00:00")
        self.assertEqual(sc.get_history(self.db, "SN000001"), before_hist)
        self.assertEqual(dict(sc.get_product(self.db, "SN000001")), before_prod)

    def test_16_history_is_append_only(self):
        self.full_chain()
        h = sc.get_history(self.db, "SN000001")
        self.assertEqual([(e["sender"], e["receiver"]) for e in h],
                         [("Manufacturer_A", "Manufacturer_A"), ("Manufacturer_A", "Distributor_A"),
                          ("Distributor_A", "Wholesaler_A"), ("Wholesaler_A", "Pharmacy_A")])

    def test_17_ledger_hook_only_on_accepted(self):
        led = FakeLedger()
        sc.register_product(self.db, dict(GOOD), "Manufacturer_A",
                            timestamp="2026-02-01 10:00:00", ledger=led)
        sc.register_product(self.db, dict(GOOD), "Manufacturer_A", ledger=led)   # duplicate
        sc.transfer_product(self.db, "SN000001", "Rogue_Actor", "Pharmacy_A", "NONE", ledger=led)
        sc.transfer_product(self.db, "SN000001", "Manufacturer_A", "Distributor_A", "AUTH-x",
                            "2026-02-02 10:00:00", ledger=led)
        self.assertEqual([e["event_type"] for e in led.events], ["REGISTER", "TRANSFER"])


class LoggingResult(unittest.TextTestResult):
    rows = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.rows.append((test._testMethodName, "PASS"))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.rows.append((test._testMethodName, "FAIL"))

    def addError(self, test, err):
        super().addError(test, err)
        self.rows.append((test._testMethodName, "ERROR"))


def run_unit_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UnitTests)
    runner = unittest.TextTestRunner(verbosity=1, resultclass=LoggingResult)
    result = runner.run(suite)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(f"{LOG_DIR}/phase2_unit_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["test_case", "status"])
        w.writerows(LoggingResult.rows)
    return result.wasSuccessful()


# ------------------------------------------------------------------ replay
# Expected outcome of replaying each scenario through the Phase 2 functions.
# None = every event accepted. Otherwise = acceptable first-rejection codes.
# (Expired products and data mismatches are NOT rejected here: they are flagged by the
#  Phase 4 verification engine.)
EXPECTED_REPLAY = {
    "legitimate": None,
    "expired_product": None,
    "data_mismatch": None,
    "duplicate_serial": {sc.DUPLICATE_SERIAL},
    "unregistered_product": {sc.UNKNOWN_PRODUCT},
    "unauthorized_transfer": {sc.UNAUTHORIZED_SENDER},
    "missing_custody_event": {sc.INVALID_OWNERSHIP, sc.INVALID_TRANSITION},
}


def read_csv(name):
    with open(f"{DATA_DIR}/{name}", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def replay_dataset():
    cases = {c["record_id"]: c for c in read_csv("test_cases.csv")}
    products = {p["record_id"]: p for p in read_csv("products.csv")}
    events = read_csv("events.csv")          # already sorted chronologically
    conn = reset_database("pharma_replay.db")
    conn.executemany("INSERT INTO participants VALUES (?,?,?)", PARTICIPANTS)
    conn.commit()

    first_reject = {}                         # record_id -> first rejection code
    accepted_count = Counter()
    for e in events:
        rid = e["record_id"]
        if e["event_type"] == "REGISTER":
            res = sc.register_product(conn, products[rid], e["sender"], e["timestamp"],
                                      e["authorization_evidence"])
        else:
            res = sc.transfer_product(conn, e["serial_number"], e["sender"], e["receiver"],
                                      e["authorization_evidence"], e["timestamp"])
        if res.ok:
            accepted_count[rid] += 1
        elif rid not in first_reject:
            first_reject[rid] = res.code

    rows = []
    for rid, c in sorted(cases.items()):
        expected = EXPECTED_REPLAY[c["scenario"]]
        actual = first_reject.get(rid)
        passed = (actual is None) if expected is None else (actual in expected)
        rows.append(dict(record_id=rid, scenario=c["scenario"],
                         expected="ALL ACCEPTED" if expected is None else "REJECT: " + "/".join(sorted(expected)),
                         actual="ALL ACCEPTED" if actual is None else "REJECT: " + actual,
                         status="PASS" if passed else "FAIL"))
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(f"{LOG_DIR}/phase2_replay_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["record_id", "scenario", "expected", "actual", "status"])
        w.writeheader()
        w.writerows(rows)

    # summary
    by_sc = defaultdict(Counter)
    for r in rows:
        by_sc[r["scenario"]][r["status"]] += 1
    print("\nReplay of dataset through register_product()/transfer_product():")
    print(f"  {'scenario':24s} {'pass':>5s} {'fail':>5s}  actual outcome")
    for s in EXPECTED_REPLAY:
        outcomes = Counter(r["actual"] for r in rows if r["scenario"] == s)
        print(f"  {s:24s} {by_sc[s]['PASS']:5d} {by_sc[s]['FAIL']:5d}  {dict(outcomes)}")
    n_pass = sum(1 for r in rows if r["status"] == "PASS")
    n_prod = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    n_ev = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"  TOTAL {n_pass}/{len(rows)} as expected | clean DB now holds {n_prod} products, {n_ev} events")
    conn.close()
    return n_pass == len(rows)


if __name__ == "__main__":
    ok_unit = run_unit_tests()
    ok_replay = replay_dataset()
    sys.exit(0 if (ok_unit and ok_replay) else 1)
