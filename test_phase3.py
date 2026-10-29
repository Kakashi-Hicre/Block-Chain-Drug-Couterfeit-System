"""
test_phase3.py - tests for Phase 3 (SHA-256 hash-linked ledger).

Part A: unit tests (block creation, linking, tamper detection, persistence, integration).
Part B: tamper-detection experiment (E4) on the ledger built from the full synthetic dataset:
        8 tamper types x 100 trials + 100 untouched controls, plus 3 documented LIMITATION
        scenarios (last-block recompute, full-chain rewrite, truncation) that a plain hash
        chain cannot detect without an external anchor.

Run:  python test_phase3.py
Writes: logs/phase3_unit_log.csv, logs/phase3_tamper_log.csv, logs/phase3_tamper_summary.csv
"""
import copy
import csv
import os
import random
import sys
import time
import unittest

from database import reset_database
from generate_dataset import PARTICIPANTS
import ledger as lg
import supply_chain as sc
from test_phase2 import GOOD, LoggingResult, fresh_db, read_csv

LOG_DIR = "logs"
SEED = 2026
TRIALS = 100


def build_ledger(n_events=6):
    L = lg.Ledger()
    for i in range(n_events):
        L.add_event(dict(event_id=i + 1, serial_number=f"SN{i:06d}", event_type="REGISTER",
                         sender="Manufacturer_A", receiver="Manufacturer_A",
                         timestamp=f"2026-02-0{i + 1} 10:00:00", authorization_evidence="AUTH"))
    return L


def codes(result):
    return {i.code for i in result.issues}


class UnitTests(unittest.TestCase):
    def test_01_genesis_block(self):
        L = lg.Ledger()
        self.assertEqual(len(L), 1)
        g = L.blocks[0]
        self.assertEqual((g.index, g.previous_hash), (0, "0" * 64))
        self.assertTrue(L.verify_chain().ok)

    def test_02_blocks_are_linked_and_indexed(self):
        L = build_ledger()
        for i, b in enumerate(L.blocks):
            self.assertEqual(b.index, i)
            if i:
                self.assertEqual(b.previous_hash, L.blocks[i - 1].hash)

    def test_03_hash_is_sha256_and_deterministic(self):
        L = build_ledger()
        b = L.blocks[3]
        self.assertEqual(len(b.hash), 64)
        self.assertEqual(b.hash, b.compute_hash())
        b2 = copy.deepcopy(b)
        b2.data = dict(reversed(list(b2.data.items())))     # key order must not matter
        self.assertEqual(b2.compute_hash(), b.hash)

    def test_04_intact_chain_verifies(self):
        self.assertTrue(build_ledger(50).verify_chain().ok)

    def test_05_modified_data_detected(self):
        L = build_ledger()
        L.blocks[3].data["receiver"] = "Pharmacy_C"
        r = L.verify_chain()
        self.assertFalse(r.ok)
        self.assertIn(lg.HASH_MISMATCH, codes(r))
        self.assertEqual(min(i.block_index for i in r.issues), 3)

    def test_06_modified_timestamp_detected(self):
        L = build_ledger()
        L.blocks[2].timestamp = "2030-01-01 00:00:00"
        self.assertIn(lg.HASH_MISMATCH, codes(L.verify_chain()))

    def test_07_modified_stored_hash_detected(self):
        L = build_ledger()
        L.blocks[2].hash = "f" * 64
        r = L.verify_chain()
        self.assertFalse(r.ok)
        self.assertIn(lg.HASH_MISMATCH, codes(r))
        self.assertIn(lg.LINK_BROKEN, codes(r))

    def test_08_modified_previous_hash_detected(self):
        L = build_ledger()
        L.blocks[4].previous_hash = "a" * 64
        self.assertFalse(L.verify_chain().ok)

    def test_09_deleted_block_detected(self):
        L = build_ledger()
        del L.blocks[3]
        r = L.verify_chain()
        self.assertFalse(r.ok)
        self.assertIn(lg.INDEX_MISMATCH, codes(r))

    def test_10_inserted_block_detected(self):
        L = build_ledger()
        prev = L.blocks[2]
        fake = lg.Block(3, "2026-02-03 12:00:00", {"serial_number": "FAKE"}, prev.hash).seal()
        L.blocks.insert(3, fake)
        self.assertFalse(L.verify_chain().ok)

    def test_11_swapped_blocks_detected(self):
        L = build_ledger()
        L.blocks[2], L.blocks[3] = L.blocks[3], L.blocks[2]
        self.assertFalse(L.verify_chain().ok)

    def test_12_modified_last_block_detected(self):
        L = build_ledger()
        L.blocks[-1].data["sender"] = "Rogue_Actor"
        self.assertIn(lg.HASH_MISMATCH, codes(L.verify_chain()))

    def test_13_LIMITATION_last_block_recomputed(self):
        """Attacker edits the NEWEST block and recomputes its hash: chain alone cannot see it."""
        L = build_ledger()
        anchor = L.anchor()
        L.blocks[-1].data["sender"] = "Rogue_Actor"
        L.blocks[-1].seal()
        self.assertTrue(L.verify_chain().ok)                       # undetected by chain alone
        r = L.verify_chain(expected_head=anchor[0], expected_length=anchor[1])
        self.assertIn(lg.HEAD_MISMATCH, codes(r))                  # caught by external anchor

    def test_14_LIMITATION_full_rewrite(self):
        """Attacker edits block 2 and recomputes every later hash: only the anchor catches it."""
        L = build_ledger()
        anchor = L.anchor()
        L.blocks[2].data["receiver"] = "Pharmacy_C"
        for i in range(2, len(L.blocks)):
            if i > 2:
                L.blocks[i].previous_hash = L.blocks[i - 1].hash
            L.blocks[i].seal()
        self.assertTrue(L.verify_chain().ok)
        self.assertIn(lg.HEAD_MISMATCH, codes(L.verify_chain(expected_head=anchor[0])))

    def test_15_LIMITATION_truncation(self):
        L = build_ledger()
        anchor = L.anchor()
        del L.blocks[-2:]
        self.assertTrue(L.verify_chain().ok)
        self.assertIn(lg.LENGTH_MISMATCH, codes(L.verify_chain(expected_length=anchor[1])))

    def test_16_persistence_and_db_tampering(self):
        conn = fresh_db()
        L = lg.Ledger(conn)
        for i in range(5):
            L.add_event(dict(event_id=i, serial_number=f"SN{i}", event_type="REGISTER",
                             sender="Manufacturer_A", receiver="Manufacturer_A",
                             timestamp=f"2026-02-0{i + 1} 10:00:00", authorization_evidence="AUTH"))
        L2 = lg.Ledger(conn)                                        # reload from SQLite
        self.assertEqual([b.hash for b in L2.blocks], [b.hash for b in L.blocks])
        self.assertTrue(L2.verify_chain().ok)
        conn.execute("UPDATE ledger_blocks SET data = replace(data, 'SN2', 'SN9') "
                     "WHERE block_index = 3")                       # tamper directly in the DB
        conn.commit()
        L2.reload()
        r = L2.verify_chain()
        self.assertFalse(r.ok)
        self.assertEqual(min(i.block_index for i in r.issues), 3)

    def test_17_integration_only_accepted_events_become_blocks(self):
        conn = fresh_db()
        L = lg.Ledger(conn)
        sc.register_product(conn, dict(GOOD), "Manufacturer_A", "2026-02-01 10:00:00", ledger=L)
        sc.register_product(conn, dict(GOOD), "Manufacturer_A", ledger=L)                # duplicate
        sc.transfer_product(conn, "SN000001", "Rogue_Actor", "Pharmacy_A", "NONE", ledger=L)
        sc.transfer_product(conn, "SN000001", "Manufacturer_A", "Distributor_A", "AUTH-x",
                            "2026-02-02 10:00:00", ledger=L)
        self.assertEqual(len(L), 3)                                  # genesis + 2 accepted
        self.assertEqual([b.data["event_type"] for b in L.blocks[1:]], ["REGISTER", "TRANSFER"])
        self.assertEqual(len(L.blocks_for_serial("SN000001")), 2)
        self.assertTrue(L.verify_chain().ok)


class LR(LoggingResult):
    rows = []


def run_unit_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UnitTests)
    result = unittest.TextTestRunner(verbosity=1, resultclass=LR).run(suite)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(f"{LOG_DIR}/phase3_unit_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["test_case", "status"])
        w.writerows(LR.rows)
    return result.wasSuccessful()


# ------------------------------------------------------------------ experiment (E4)
def build_dataset_ledger():
    """Replay the synthetic dataset through supply_chain with the ledger attached."""
    cases = read_csv("test_cases.csv")
    products = {p["record_id"]: p for p in read_csv("products.csv")}
    events = read_csv("events.csv")
    conn = reset_database("pharma_replay.db")
    conn.executemany("INSERT INTO participants VALUES (?,?,?)", PARTICIPANTS)
    conn.commit()
    L = lg.Ledger(conn)
    accepted = rejected = 0
    for e in events:
        if e["event_type"] == "REGISTER":
            res = sc.register_product(conn, products[e["record_id"]], e["sender"], e["timestamp"],
                                      e["authorization_evidence"], ledger=L)
        else:
            res = sc.transfer_product(conn, e["serial_number"], e["sender"], e["receiver"],
                                      e["authorization_evidence"], e["timestamp"], ledger=L)
        accepted += res.ok
        rejected += (not res.ok)
    return conn, L, accepted, rejected


def t_modify_data(blocks, rng):
    i = rng.randrange(1, len(blocks))
    blocks[i].data["receiver"] = "Tampered_Receiver"
    return i


def t_modify_timestamp(blocks, rng):
    i = rng.randrange(1, len(blocks))
    blocks[i].timestamp = "2031-12-31 23:59:59"
    return i


def t_modify_hash(blocks, rng):
    i = rng.randrange(1, len(blocks))
    h = blocks[i].hash
    blocks[i].hash = ("0" if h[0] != "0" else "1") + h[1:]
    return i


def t_modify_prev_hash(blocks, rng):
    i = rng.randrange(1, len(blocks))
    h = blocks[i].previous_hash
    blocks[i].previous_hash = ("0" if h[0] != "0" else "1") + h[1:]
    return i


def t_delete_block(blocks, rng):
    i = rng.randrange(1, len(blocks) - 1)          # middle block (deleting the newest = truncation)
    del blocks[i]
    return i


def t_insert_block(blocks, rng):
    i = rng.randrange(1, len(blocks))
    fake = lg.Block(i, blocks[i - 1].timestamp, {"serial_number": "FORGED", "event_type": "TRANSFER"},
                    blocks[i - 1].hash).seal()
    blocks.insert(i, fake)
    return i


def t_swap_adjacent(blocks, rng):
    i = rng.randrange(1, len(blocks) - 1)
    blocks[i], blocks[i + 1] = blocks[i + 1], blocks[i]
    return i


def t_modify_last(blocks, rng):
    blocks[-1].data["sender"] = "Tampered_Sender"
    return len(blocks) - 1


TAMPERS = [
    ("T1 modify event data", t_modify_data), ("T2 modify timestamp", t_modify_timestamp),
    ("T3 modify stored hash", t_modify_hash), ("T4 modify previous_hash", t_modify_prev_hash),
    ("T5 delete middle block", t_delete_block), ("T6 insert forged block", t_insert_block),
    ("T7 swap adjacent blocks", t_swap_adjacent), ("T8 modify newest block", t_modify_last),
]


def l_last_recompute(blocks, rng):
    blocks[-1].data["sender"] = "Tampered_Sender"
    blocks[-1].seal()
    return len(blocks) - 1


def l_full_rewrite(blocks, rng):
    i = rng.randrange(1, len(blocks) - 1)
    blocks[i].data["receiver"] = "Tampered_Receiver"
    for j in range(i, len(blocks)):
        if j > i:
            blocks[j].previous_hash = blocks[j - 1].hash
        blocks[j].seal()
    return i


def l_truncate(blocks, rng):
    k = rng.randint(1, 10)
    del blocks[-k:]
    return len(blocks)


LIMITS = [("L1 newest block edited + hash recomputed", l_last_recompute),
          ("L2 full-chain rewrite (all later hashes recomputed)", l_full_rewrite),
          ("L3 newest blocks truncated", l_truncate)]


def run_experiment():
    conn, L, accepted, rejected = build_dataset_ledger()
    n = len(L)
    print(f"\nLedger built from dataset: {accepted} accepted events -> {n} blocks "
          f"(incl. genesis); {rejected} rejected events never reached the ledger")

    t0 = time.perf_counter()
    base = L.verify_chain()
    t_verify = time.perf_counter() - t0
    print(f"Baseline verify_chain on {n} blocks: ok={base.ok} in {t_verify * 1000:.1f} ms")

    # a persisted ledger reloaded from SQLite must verify identically
    reloaded = lg.Ledger(conn)
    print(f"Reloaded from SQLite: {len(reloaded)} blocks, ok={reloaded.verify_chain().ok}")

    anchor_head, anchor_len = L.anchor()
    rng = random.Random(SEED)
    rows = []

    for t in range(TRIALS):                       # controls: untouched copies (false-positive check)
        r = lg.verify_blocks(copy.deepcopy(L.blocks), anchor_head, anchor_len)
        rows.append(dict(group="CONTROL untouched", trial=t, block_index="", detected_chain=int(not r.ok),
                         detected_with_anchor=int(not r.ok)))
    for name, fn in TAMPERS + LIMITS:
        for t in range(TRIALS):
            blocks = copy.deepcopy(L.blocks)
            idx = fn(blocks, rng)
            r_chain = lg.verify_blocks(blocks)
            r_anchor = lg.verify_blocks(blocks, anchor_head, anchor_len)
            rows.append(dict(group=name, trial=t, block_index=idx, detected_chain=int(not r_chain.ok),
                             detected_with_anchor=int(not r_anchor.ok)))

    os.makedirs(LOG_DIR, exist_ok=True)
    with open(f"{LOG_DIR}/phase3_tamper_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["group", "trial", "block_index", "detected_chain",
                                          "detected_with_anchor"])
        w.writeheader()
        w.writerows(rows)

    summary = []
    print(f"\n{'scenario':56s} {'trials':>6s} {'chain only':>11s} {'with anchor':>12s}")
    for name in ["CONTROL untouched"] + [t[0] for t in TAMPERS] + [l[0] for l in LIMITS]:
        g = [r for r in rows if r["group"] == name]
        c = sum(r["detected_chain"] for r in g)
        a = sum(r["detected_with_anchor"] for r in g)
        summary.append(dict(scenario=name, trials=len(g), detected_chain_only=c,
                            rate_chain_only=f"{100 * c / len(g):.1f}%", detected_with_anchor=a,
                            rate_with_anchor=f"{100 * a / len(g):.1f}%"))
        print(f"{name:56s} {len(g):6d} {c:5d} ({100 * c / len(g):5.1f}%) {a:5d} ({100 * a / len(g):5.1f}%)")
    with open(f"{LOG_DIR}/phase3_tamper_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)

    ctrl = next(s for s in summary if s["scenario"].startswith("CONTROL"))
    tamper_ok = all(s["detected_chain_only"] == s["trials"] for s in summary if s["scenario"][0] == "T")
    limit_ok = all(s["detected_chain_only"] == 0 and s["detected_with_anchor"] == s["trials"]
                   for s in summary if s["scenario"][0] == "L")
    ok = base.ok and reloaded.verify_chain().ok and ctrl["detected_chain_only"] == 0 \
        and ctrl["detected_with_anchor"] == 0 and tamper_ok and limit_ok
    conn.close()
    return ok


if __name__ == "__main__":
    ok_unit = run_unit_tests()
    ok_exp = run_experiment()
    print("\nRESULT:", "ALL PASSED" if (ok_unit and ok_exp) else "FAILURES PRESENT")
    sys.exit(0 if (ok_unit and ok_exp) else 1)
