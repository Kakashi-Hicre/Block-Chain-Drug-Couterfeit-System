"""
test_phase4.py - tests and evaluation for Phase 4 (verification engine).

Part A: unit tests - one or more per rule, on small in-memory databases.
Part B: evaluation on the synthetic dataset (pharma_traceability.db), per SERIAL NUMBER:
          B1 multi-class result vs ground truth + confusion matrix + binary detection / false-positive rate
          B2 E6-style experiment: delete one custody event from legitimate products
          B3 E7-style experiment: alter one product field of legitimate products
          B4 ledger mode: legitimate products verify cleanly; DB-vs-ledger tampering is detected

Ground truth note: a serial number that was registered twice is a DUPLICATE_SERIAL case at serial
level - the original record that shares the serial is flagged too, because the engine (correctly)
cannot tell which of the two registrations is the real one.

Run:  python test_phase4.py
Writes: logs/phase4_unit_log.csv, phase4_evaluation_log.csv, phase4_confusion_matrix.csv,
        phase4_summary.csv
"""
import csv
import json
import os
import random
import sqlite3
import sys
import unittest
from collections import Counter
from datetime import date

import ledger as lg
import supply_chain as sc
import verifier as vf
from test_phase2 import GOOD, LoggingResult, fresh_db, read_csv
from test_phase3 import build_dataset_ledger

LOG_DIR = "logs"
REF = date(2026, 6, 1)
SEED = 4242
TRIALS = 200

BATCHES = [("BATCH2026A", "DRG001", "Sample Antibiotic A", "Manufacturer_A", "2026-01-10", "2028-01-10"),
           ("BATCHOLD1", "DRG004", "Sample Antiviral D", "Manufacturer_A", "2024-03-01", "2026-03-01")]
CHAIN = [("Manufacturer_A", "Distributor_A"), ("Distributor_A", "Wholesaler_A"),
         ("Wholesaler_A", "Pharmacy_A")]


def add_product(conn, L, serial, stage=3, **fields):
    p = dict(GOOD, serial_number=serial, **fields)
    assert sc.register_product(conn, p, "Manufacturer_A", "2026-02-01 10:00:00", ledger=L).ok
    for k, (s, r) in enumerate(CHAIN[:stage]):
        assert sc.transfer_product(conn, serial, s, r, f"AUTH-{s}-{serial}",
                                   f"2026-02-0{k + 2} 10:00:00", ledger=L).ok


def world(ledger=False, stage=3, serial="SN000001"):
    conn = fresh_db()
    conn.executemany("INSERT INTO batches VALUES (?,?,?,?,?,?)", BATCHES)
    conn.commit()
    L = lg.Ledger(conn) if ledger else None
    add_product(conn, L, serial, stage)
    return conn, L


def engine(conn, L=None, **kw):
    return vf.VerificationEngine(conn, L, reference_date=REF, **kw)


def rules(rep):
    return {f["rule"] for f in rep["findings"]}


def add_event(conn, serial, etype, sender, receiver, ts, evidence="AUTH-x"):
    conn.execute("INSERT INTO events (serial_number, event_type, sender, receiver, timestamp, "
                 "authorization_evidence) VALUES (?,?,?,?,?,?)",
                 (serial, etype, sender, receiver, ts, evidence))


def set_owner(conn, serial, owner, status):
    conn.execute("UPDATE products SET current_owner=?, current_status=? WHERE serial_number=?",
                 (owner, status, serial))
    conn.commit()


class UnitTests(unittest.TestCase):
    # ---- clean records
    def test_01_verified_full_chain(self):
        conn, _ = world()
        rep = engine(conn).verify_serial("SN000001")
        self.assertEqual((rep["status"], rep["findings"], rep["suspicious"]),
                         (vf.RECORD_VERIFIED, [], False))
        self.assertIn("not prove", rep["message"])
        self.assertEqual(len(rep["history"]), 4)

    def test_02_verified_registered_only(self):
        conn, _ = world(stage=0)
        self.assertEqual(engine(conn).verify_serial("SN000001")["status"], vf.RECORD_VERIFIED)

    # ---- one test per rule
    def test_03_unregistered(self):
        conn, _ = world()
        rep = engine(conn).verify_serial("SN999999")
        self.assertEqual((rep["status"], rep["product"]), (vf.UNREGISTERED, None))

    def test_04_unregistered_with_events(self):
        conn, _ = world()
        add_event(conn, "SN900001", "TRANSFER", "Distributor_A", "Wholesaler_A", "2026-03-01 10:00:00")
        conn.commit()
        rep = engine(conn).verify_serial("SN900001")
        self.assertEqual(rep["status"], vf.UNREGISTERED)
        self.assertIn("1 event", rep["findings"][0]["message"])

    def test_05_duplicate_serial(self):
        conn, _ = world()
        add_event(conn, "SN000001", "REGISTER", "Manufacturer_B", "Manufacturer_B", "2026-03-01 10:00:00")
        conn.commit()
        rep = engine(conn).verify_serial("SN000001")
        self.assertEqual((rep["status"], rules(rep)), (vf.DUPLICATE, {vf.DUPLICATE}))

    def test_06_unauthorized_sender_reported_once(self):
        conn, _ = world(stage=1)
        add_event(conn, "SN000001", "TRANSFER", "Rogue_Actor", "Pharmacy_A", "2026-02-10 10:00:00", "NONE")
        set_owner(conn, "SN000001", "Pharmacy_A", "At pharmacy")
        rep = engine(conn).verify_serial("SN000001")
        self.assertEqual(rules(rep), {vf.UNAUTHORIZED})            # no extra traceability noise
        self.assertEqual(len(rep["findings"]), 1)

    def test_07_missing_evidence(self):
        conn, _ = world(stage=1)
        conn.execute("UPDATE events SET authorization_evidence='NONE' WHERE event_type='TRANSFER'")
        conn.commit()
        self.assertEqual(rules(engine(conn).verify_serial("SN000001")), {vf.UNAUTHORIZED})

    def test_08_registration_by_non_manufacturer(self):
        conn, _ = world(stage=0)
        conn.execute("UPDATE events SET sender='Distributor_A', receiver='Distributor_A'")
        conn.commit()
        self.assertIn(vf.UNAUTHORIZED, rules(engine(conn).verify_serial("SN000001")))

    def test_09_expired_is_not_counterfeit(self):
        conn, _ = world()
        add_product(conn, None, "SN000002", 3, batch_number="BATCHOLD1", drug_id="DRG004",
                    product_name="Sample Antiviral D", manufacturing_date="2024-03-01",
                    expiry_date="2026-03-01")
        rep = engine(conn).verify_serial("SN000002")
        self.assertEqual((rep["status"], rules(rep), rep["suspicious"], rep["flagged"]),
                         (vf.EXPIRY, {vf.EXPIRY}, False, True))
        self.assertIn("does not by itself indicate counterfeiting", rep["message"])

    def test_10_skipped_wholesaler(self):
        conn, _ = world(stage=1)
        add_event(conn, "SN000001", "TRANSFER", "Distributor_A", "Pharmacy_A", "2026-02-05 10:00:00")
        set_owner(conn, "SN000001", "Pharmacy_A", "At pharmacy")
        rep = engine(conn).verify_serial("SN000001")
        self.assertEqual(rules(rep), {vf.TRACEABILITY})
        self.assertIn("skips", rep["findings"][0]["message"])

    def test_11_missing_first_transfer(self):
        conn, _ = world(stage=0)
        add_event(conn, "SN000001", "TRANSFER", "Distributor_A", "Wholesaler_A", "2026-02-05 10:00:00")
        set_owner(conn, "SN000001", "Wholesaler_A", "At wholesaler")
        self.assertEqual(rules(engine(conn).verify_serial("SN000001")), {vf.TRACEABILITY})

    def test_12_deleted_last_event(self):
        conn, _ = world()
        conn.execute("DELETE FROM events WHERE event_id = (SELECT MAX(event_id) FROM events)")
        conn.commit()
        self.assertEqual(rules(engine(conn).verify_serial("SN000001")), {vf.TRACEABILITY})

    def test_13_deleted_registration_event(self):
        conn, _ = world()
        conn.execute("DELETE FROM events WHERE event_type='REGISTER'")
        conn.commit()
        rep = engine(conn).verify_serial("SN000001")
        self.assertIn(vf.TRACEABILITY, rules(rep))
        self.assertIn("registration", rep["findings"][0]["message"])

    def test_14_data_mismatch_variants(self):
        for field, value in [("manufacturer", "Manufacturer_B"), ("expiry_date", "2030-01-01"),
                             ("drug_id", "DRG999"), ("batch_number", "BATCH_FAKE")]:
            conn, _ = world()
            conn.execute(f"UPDATE products SET {field}=?", (value,))
            conn.commit()
            rep = engine(conn).verify_serial("SN000001")
            self.assertIn(vf.MISMATCH, rules(rep), field)
            self.assertEqual(rep["status"], vf.MISMATCH, field)

    # ---- priority + reporting
    def test_15_priority_and_all_findings_kept(self):
        conn, _ = world()
        add_product(conn, None, "SN000002", 3, batch_number="BATCHOLD1", drug_id="DRG004",
                    product_name="Sample Antiviral D", manufacturing_date="2024-03-01",
                    expiry_date="2026-03-01")
        add_event(conn, "SN000002", "REGISTER", "Manufacturer_B", "Manufacturer_B", "2026-03-01 10:00:00")
        conn.commit()
        rep = engine(conn).verify_serial("SN000002")
        self.assertEqual(rep["status"], vf.DUPLICATE)
        self.assertEqual(rules(rep), {vf.DUPLICATE, vf.EXPIRY})

    def test_16_report_text(self):
        conn, _ = world()
        conn.execute("UPDATE products SET manufacturer='Manufacturer_B'")
        conn.commit()
        text = vf.VerificationEngine.format_report(engine(conn).verify_serial("SN000001"))
        for needle in ("SN000001", vf.MISMATCH, "Failed rules", "Event history",
                       "not proof of counterfeiting"):
            self.assertIn(needle, text)

    # ---- ledger mode
    def test_17_ledger_clean(self):
        conn, L = world(ledger=True)
        add_product(conn, L, "SN000002", 2)
        eng = engine(conn, L)
        self.assertTrue(eng.check_ledger()["ok"])
        for s in ("SN000001", "SN000002"):
            self.assertEqual(eng.verify_serial(s)["status"], vf.RECORD_VERIFIED)

    def test_18_database_tampered_vs_ledger(self):
        conn, L = world(ledger=True)
        conn.execute("UPDATE events SET receiver='Distributor_B' WHERE event_type='TRANSFER' "
                     "AND receiver='Distributor_A'")
        conn.commit()
        rep = engine(conn, L).verify_serial("SN000001")
        self.assertIn(vf.BLOCKCHAIN, rules(rep))
        self.assertEqual(rep["status"], vf.BLOCKCHAIN)

    def test_19_ledger_block_tampered_is_local(self):
        conn, L = world(ledger=True)
        add_product(conn, L, "SN000002", 3)
        add_product(conn, L, "SN000003", 3)                          # blocks 9-12, far from block 2
        L.blocks[2].data["receiver"] = "Distributor_B"
        eng = engine(conn, L)
        self.assertFalse(eng.check_ledger()["ok"])
        self.assertIn(vf.BLOCKCHAIN, rules(eng.verify_serial("SN000001")))
        self.assertEqual(eng.verify_serial("SN000003")["status"], vf.RECORD_VERIFIED)

    def test_20_truncation_caught_with_anchor(self):
        conn, L = world(ledger=True)
        anchor = L.anchor()
        del L.blocks[-2:]
        eng = engine(conn, L, expected_anchor=anchor)
        self.assertFalse(eng.check_ledger()["ok"])
        self.assertIn(vf.BLOCKCHAIN, rules(eng.verify_serial("SN000001")))


class LR4(LoggingResult):
    rows = []


def run_unit_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UnitTests)
    result = unittest.TextTestRunner(verbosity=1, resultclass=LR4).run(suite)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(f"{LOG_DIR}/phase4_unit_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["test_case", "status"])
        w.writerows(LR4.rows)
    return result.wasSuccessful()


# ------------------------------------------------------------------ dataset evaluation
EXPECTED_STATUS = {
    "legitimate": vf.RECORD_VERIFIED, "expired_product": vf.EXPIRY,
    "duplicate_serial": vf.DUPLICATE, "unregistered_product": vf.UNREGISTERED,
    "unauthorized_transfer": vf.UNAUTHORIZED, "missing_custody_event": vf.TRACEABILITY,
    "data_mismatch": vf.MISMATCH,
}


def serial_ground_truth():
    cases = read_csv("test_cases.csv")
    dup = {c["serial_number"] for c in cases if c["scenario"] == "duplicate_serial"}
    return {c["serial_number"]: ("duplicate_serial" if c["serial_number"] in dup else c["scenario"])
            for c in cases}


def memory_copy(path):
    src = sqlite3.connect(path)
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    mem.row_factory = sqlite3.Row
    return mem


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def run_dataset_evaluation(summary):
    truth = serial_ground_truth()
    mem = memory_copy("pharma_traceability.db")
    eng = vf.VerificationEngine(mem, reference_date=REF)

    rows, matrix = [], Counter()
    for serial, scen in sorted(truth.items()):
        rep = eng.verify_serial(serial)
        exp = EXPECTED_STATUS[scen]
        rows.append(dict(serial_number=serial, ground_truth=scen, expected=exp, actual=rep["status"],
                         all_rules="|".join(sorted(rules(rep))), flagged=int(rep["flagged"]),
                         match="PASS" if rep["status"] == exp else "FAIL"))
        matrix[(exp, rep["status"])] += 1
    write_csv(f"{LOG_DIR}/phase4_evaluation_log.csv", rows,
              ["serial_number", "ground_truth", "expected", "actual", "all_rules", "flagged", "match"])

    labels = [vf.RECORD_VERIFIED] + [s for s in vf.PRIORITY if s != vf.BLOCKCHAIN]
    with open(f"{LOG_DIR}/phase4_confusion_matrix.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["expected \\ actual"] + labels)
        for e in labels:
            w.writerow([e] + [matrix[(e, a)] for a in labels])

    n = len(rows)
    n_pass = sum(r["match"] == "PASS" for r in rows)
    print(f"\nB1. Dataset evaluation on {n} distinct serial numbers (multi-class): {n_pass}/{n} correct")
    print(f"  {'scenario':24s} {'serials':>7s} {'correct':>8s}  actual statuses")
    for scen in EXPECTED_STATUS:
        g = [r for r in rows if r["ground_truth"] == scen]
        print(f"  {scen:24s} {len(g):7d} {sum(r['match'] == 'PASS' for r in g):8d}  "
              f"{dict(Counter(r['actual'] for r in g))}")
    anomalies = [r for r in rows if r["ground_truth"] != "legitimate"]
    legit = [r for r in rows if r["ground_truth"] == "legitimate"]
    tp = sum(r["flagged"] for r in anomalies)
    fp = sum(r["flagged"] for r in legit)
    print(f"  Binary (any finding = flagged): TP={tp} FN={len(anomalies) - tp} FP={fp} "
          f"TN={len(legit) - fp} | detection rate {pct(tp, len(anomalies))}, "
          f"false-positive rate {pct(fp, len(legit))}")
    summary += [("B1 serials evaluated", n, n_pass, pct(n_pass, n)),
                ("B1 anomaly detection (any finding)", len(anomalies), tp, pct(tp, len(anomalies))),
                ("B1 false positives on legitimate", len(legit), fp, pct(fp, len(legit)))]
    ok = n_pass == n and fp == 0

    # B2 / B3: mutate legitimate products one at a time, roll back after each trial
    rng = random.Random(SEED)
    legit_serials = [r["serial_number"] for r in legit]
    det6 = det7 = 0
    for _ in range(TRIALS):
        s = rng.choice(legit_serials)
        ev = rng.choice(sc.get_history(mem, s))
        mem.execute("DELETE FROM events WHERE event_id = ?", (ev["event_id"],))
        det6 += vf.TRACEABILITY in rules(eng.verify_serial(s))
        mem.rollback()
    for _ in range(TRIALS):
        s = rng.choice(legit_serials)
        prod = sc.get_product(mem, s)
        field = rng.choice(["drug_id", "product_name", "manufacturer", "manufacturing_date",
                            "expiry_date", "batch_number"])
        new = {"drug_id": "DRG999", "product_name": "Altered Product", "manufacturing_date": "2025-05-05",
               "expiry_date": "2031-01-01", "batch_number": "BATCH_FAKE",
               "manufacturer": "Manufacturer_B" if prod["manufacturer"] == "Manufacturer_A"
               else "Manufacturer_A"}[field]
        mem.execute(f"UPDATE products SET {field} = ? WHERE serial_number = ?", (new, s))
        det7 += vf.MISMATCH in rules(eng.verify_serial(s))
        mem.rollback()
    base_ok = all(eng.verify_serial(s)["status"] == vf.RECORD_VERIFIED for s in legit_serials[:100])
    print(f"B2. Delete one custody event from legitimate products: {det6}/{TRIALS} flagged "
          f"as TRACEABILITY_WARNING ({pct(det6, TRIALS)})")
    print(f"B3. Alter one product field of legitimate products:   {det7}/{TRIALS} flagged "
          f"as DATA_MISMATCH_ALERT ({pct(det7, TRIALS)}) | database restored cleanly: {base_ok}")
    summary += [("B2 missing-event mutation (E6)", TRIALS, det6, pct(det6, TRIALS)),
                ("B3 data-mismatch mutation (E7)", TRIALS, det7, pct(det7, TRIALS))]
    mem.close()
    return ok and det6 == TRIALS and det7 == TRIALS and base_ok


def run_ledger_evaluation(summary):
    conn, L, accepted, rejected = build_dataset_ledger()          # writes pharma_replay.db
    conn.executemany("INSERT INTO batches VALUES (:batch_number,:drug_id,:product_name,:manufacturer,"
                     ":manufacturing_date,:expiry_date)", read_csv("batches.csv"))   # master data
    conn.commit()
    truth = serial_ground_truth()
    legit = sorted(s for s, sc_ in truth.items() if sc_ == "legitimate")
    eng = vf.VerificationEngine(conn, L, reference_date=REF)
    clean = sum(eng.verify_serial(s)["status"] == vf.RECORD_VERIFIED for s in legit)
    print(f"\nB4. Ledger mode ({len(L)} blocks): {clean}/{len(legit)} legitimate serials verify "
          f"with no integrity alert; ledger check ok = {eng.check_ledger()['ok']}")
    summary.append(("B4 legitimate serials clean in ledger mode", len(legit), clean, pct(clean, len(legit))))
    conn.close()

    rng = random.Random(SEED)
    results = {"LT1 ledger block altered in the database": 0,
               "LT2 database event altered (ledger intact)": 0}
    for _ in range(TRIALS):
        for key in results:
            mem = memory_copy("pharma_replay.db")
            led = lg.Ledger(mem)
            s = rng.choice(legit)
            positions = [p for p, b in enumerate(led.blocks) if b.data.get("serial_number") == s]
            if key.startswith("LT1"):
                pos = rng.choice(positions)
                d = json.loads(mem.execute("SELECT data FROM ledger_blocks WHERE block_index=?",
                                           (pos,)).fetchone()[0])
                d["receiver"] = "Tampered_Receiver"
                mem.execute("UPDATE ledger_blocks SET data=? WHERE block_index=?",
                            (json.dumps(d, sort_keys=True), pos))
            else:
                ev = rng.choice(sc.get_history(mem, s))
                new = "Pharmacy_C" if ev["receiver"] != "Pharmacy_C" else "Pharmacy_B"
                mem.execute("UPDATE events SET receiver=? WHERE event_id=?", (new, ev["event_id"]))
            mem.commit()
            e2 = vf.VerificationEngine(mem, lg.Ledger(mem), reference_date=REF)   # reload from DB
            results[key] += vf.BLOCKCHAIN in rules(e2.verify_serial(s))
            mem.close()
    ok = clean == len(legit)
    for key, v in results.items():
        print(f"  {key}: {v}/{TRIALS} flagged as BLOCKCHAIN_INTEGRITY_ALERT ({pct(v, TRIALS)})")
        summary.append((key, TRIALS, v, pct(v, TRIALS)))
        ok = ok and v == TRIALS
    return ok


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists("pharma_traceability.db"):
        sys.exit("pharma_traceability.db not found - run generate_dataset.py first")
    ok_unit = run_unit_tests()
    summary = []
    ok_data = run_dataset_evaluation(summary)
    ok_ledger = run_ledger_evaluation(summary)
    write_csv(f"{LOG_DIR}/phase4_summary.csv",
              [dict(experiment=a, trials=b, detected_or_correct=c, rate=d) for a, b, c, d in summary],
              ["experiment", "trials", "detected_or_correct", "rate"])
    ok = ok_unit and ok_data and ok_ledger
    print("\nRESULT:", "ALL PASSED" if ok else "FAILURES PRESENT")
    sys.exit(0 if ok else 1)
