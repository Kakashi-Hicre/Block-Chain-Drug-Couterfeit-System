"""
experiments.py - Phase 6: systematic experiments E1-E8, SQLite baseline comparison, evaluation
metrics and charts.

All data is SYNTHETIC. Every number in the logs comes from an actual run of this script; nothing
is typed in. Run:
    python experiments.py            full run (a few minutes)
    python experiments.py --quick    small trial counts, only to check that the script works

Writes (logs/):
    phase6_test_cases.csv          every test: experiment, test case, expected, actual, PASS/FAIL
    phase6_experiment_summary.csv  tests / passed / failed per experiment
    phase6_metrics.csv             detection rate, false-positive rate, integrity rate, completeness ...
    phase6_confusion_matrix.csv    dataset-level multi-class confusion matrix
    phase6_tamper_comparison.csv   SQLite baseline vs hash-linked ledger on database-level tampering
    phase6_performance_runs.csv    every performance run   (E8)
    phase6_performance_summary.csv mean/std/p95 per system and event count
    phase6_baseline_comparison.csv overhead of the ledger relative to the baseline
    phase6_environment.txt         machine / library versions the timings were measured on
and figures/*.png (charts for the report).

Wording: "detected" means a record was FLAGGED as suspicious. A flag is an indication that needs
investigation, not proof of counterfeiting; "record verified" does not prove physical authenticity.
"""
import argparse
import csv
import os
import platform
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import ledger as lg
import supply_chain as sc
import verifier as vf
from baseline import SQLiteBaseline
from controller import Controller
from database import create_schema, get_connection
from generate_dataset import PARTICIPANTS
from test_phase2 import read_csv
from test_phase3 import LIMITS, TAMPERS
from test_phase4 import EXPECTED_STATUS, serial_ground_truth

LOG_DIR, FIG_DIR = "logs", "figures"
REF = date(2026, 6, 1)
SEED = 6006

FULL = dict(trials=100, e3_each=40, e2_products=200, e5=200, e6=300, e7=300, sizes=[100, 500, 1000, 5000],
            repeats=5, query_samples=200)
QUICK = dict(trials=12, e3_each=6, e2_products=30, e5=24, e6=24, e7=24, sizes=[100, 500], repeats=2,
             query_samples=40)

TEST_ROWS, METRICS = [], []


def case(exp, name, expected, actual):
    ok = expected == actual
    TEST_ROWS.append(dict(experiment=exp, test_case=name, expected=expected, actual=actual,
                          status="PASS" if ok else "FAIL"))
    return ok


def metric(exp, name, successes, n, note=""):
    METRICS.append(dict(experiment=exp, measure=name, successes=successes, n=n,
                        rate=f"{100 * successes / n:.1f}%" if n else "n/a", note=note))


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


class Blockchain(SQLiteBaseline):
    """Same system as the baseline PLUS the SHA-256 hash-linked ledger."""
    name = "blockchain"

    def __init__(self, conn, reference_date=REF):
        super().__init__(conn, reference_date)
        self.ledger = lg.Ledger(conn)


# --------------------------------------------------------------------------- helpers
def new_db(path=":memory:"):
    conn = get_connection(path)
    create_schema(conn)
    conn.executemany("INSERT INTO participants VALUES (?,?,?)", PARTICIPANTS)
    conn.executemany("INSERT INTO batches VALUES (:batch_number,:drug_id,:product_name,:manufacturer,"
                     ":manufacturing_date,:expiry_date)", read_csv("batches.csv"))
    conn.commit()
    return conn


def make_ops(n_events, serial_start=1):
    """Deterministic stream of n_events valid events (register + 3 transfers per product)."""
    batches = [b for b in read_csv("batches.csv") if b["batch_number"].startswith("BATCH2026")]
    dists, whs, phs = ["Distributor_A", "Distributor_B"], ["Wholesaler_A", "Wholesaler_B"], \
        ["Pharmacy_A", "Pharmacy_B", "Pharmacy_C"]
    ops, p, t0 = [], serial_start - 1, datetime(2026, 3, 1)
    while len(ops) < n_events:
        p += 1
        serial, b = f"SN{p:06d}", batches[p % len(batches)]
        prod = {k: b[k] for k in ("drug_id", "product_name", "manufacturer", "manufacturing_date",
                                  "expiry_date", "batch_number")}
        prod["serial_number"] = serial
        path = [b["manufacturer"], dists[p % 2], whs[p % 2], phs[p % 3]]
        ops.append(("REGISTER", prod, b["manufacturer"],
                    (t0 + timedelta(seconds=len(ops))).strftime(sc.FMT_TS), f"AUTH-{path[0]}-{serial}"))
        for s, r in zip(path, path[1:]):
            if len(ops) >= n_events:
                break
            ops.append(("TRANSFER", serial, s, r, f"AUTH-{s}-{serial}",
                        (t0 + timedelta(seconds=len(ops))).strftime(sc.FMT_TS)))
    return ops


def apply_op(system, op):
    if op[0] == "REGISTER":
        return system.register(op[1], op[2], op[3], op[4])
    return system.transfer(op[1], op[2], op[3], op[4], op[5])


def op_serial(op):
    return op[1]["serial_number"] if op[0] == "REGISTER" else op[1]


def roles_of(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT participant_id, role FROM participants")}


def authorized_ids(conn):
    return [r[0] for r in conn.execute("SELECT participant_id FROM participants WHERE authorized=1 "
                                       "ORDER BY participant_id")]


def mem_copy(path):
    src = sqlite3.connect(path)
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    mem.row_factory = sqlite3.Row
    return mem


def fast_copy(blocks):
    return [lg.Block(b.index, b.timestamp, dict(b.data), b.previous_hash, b.hash) for b in blocks]


def rules_of(rep):
    return {f["rule"] for f in rep["findings"]}


def actual_label(rules, target):
    return target if target in rules else (", ".join(sorted(rules)) or "NO FINDING")


# --------------------------------------------------------------------------- E1
def run_e1():
    exp = "E1 Product registration"
    conn = new_db()
    system = Blockchain(conn)
    truth = {c["record_id"]: c["scenario"] for c in read_csv("test_cases.csv")}
    reg_ev = {e["record_id"]: e for e in read_csv("events.csv") if e["event_type"] == "REGISTER"}
    products = sorted(read_csv("products.csv"),
                      key=lambda p: (reg_ev[p["record_id"]]["timestamp"], p["record_id"]))
    for p in products:                                      # all 960 dataset registration records
        e = reg_ev[p["record_id"]]
        res = system.register(p, e["sender"], e["timestamp"], e["authorization_evidence"])
        expected = sc.DUPLICATE_SERIAL if truth[p["record_id"]] == "duplicate_serial" else sc.OK
        case(exp, f"{p['serial_number']} ({truth[p['record_id']]})", expected, res.code)

    base = dict(drug_id="DRG001", product_name="Sample Antibiotic A", batch_number="BATCH2026A",
                manufacturer="Manufacturer_A", manufacturing_date="2026-01-10", expiry_date="2028-01-10")
    extra = 0
    for i, field in enumerate(sc.REQUIRED_FIELDS):          # each required field blank
        p = dict(base, serial_number=f"SN8{i:05d}")
        p[field] = ""
        res = system.register(p, "Manufacturer_A", "2026-02-01 10:00:00")
        case(exp, f"blank {field}", sc.MISSING_FIELD, res.code)
        extra += 1
    for i, (mfg, exd, label) in enumerate([("2026/01/10", "2028-01-10", "bad date format"),
                                           ("2026-01-10", "2025-01-10", "expiry before manufacture"),
                                           ("2026-01-10", "not-a-date", "expiry not a date")]):
        res = system.register(dict(base, serial_number=f"SN81{i:03d}", manufacturing_date=mfg,
                                   expiry_date=exd), "Manufacturer_A", "2026-02-01 10:00:00")
        case(exp, label, sc.INVALID_DATE, res.code)
        extra += 1
    for i, who in enumerate(["Distributor_A", "Wholesaler_B", "Pharmacy_C", "Rogue_Actor", "Nobody"]):
        res = system.register(dict(base, serial_number=f"SN82{i:03d}"), who, "2026-02-01 10:00:00")
        case(exp, f"registration by {who}", sc.UNAUTHORIZED_REGISTRANT, res.code)
        extra += 1

    rows = [r for r in TEST_ROWS if r["experiment"] == exp]
    metric(exp, "Registration correctness (actual = expected)", sum(r["status"] == "PASS" for r in rows),
           len(rows), f"{len(products)} dataset records + {extra} invalid-input cases")
    dups = [r for r in rows if r["expected"] == sc.DUPLICATE_SERIAL]
    metric(exp, "Duplicate serial numbers rejected at registration",
           sum(r["status"] == "PASS" for r in dups), len(dups))
    n_prod = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    traceable = conn.execute(
        "SELECT COUNT(*) FROM products p WHERE (SELECT COUNT(*) FROM events e WHERE "
        "e.serial_number = p.serial_number AND e.event_type='REGISTER') = 1").fetchone()[0]
    in_ledger = sum(1 for s in [r[0] for r in conn.execute("SELECT serial_number FROM products")]
                    if len(system.ledger.blocks_for_serial(s)) == 1)
    metric(exp, "Registered products with exactly one traceable starting event (database)", traceable, n_prod)
    metric(exp, "Registered products with exactly one registration block (ledger)", in_ledger, n_prod)
    conn.close()


# --------------------------------------------------------------------------- E2
def run_e2(cfg):
    exp = "E2 Traceability"
    n = cfg["e2_products"]
    conn = new_db()
    system = Blockchain(conn)
    for op in make_ops(n * 4):
        assert apply_op(system, op).ok
    eng = system.engine()
    roles = roles_of(conn)
    want = ["Manufacturer", "Distributor", "Wholesaler", "Pharmacy"]
    complete = 0
    for s in [r[0] for r in conn.execute("SELECT serial_number FROM products ORDER BY 1")]:
        h = sc.get_history(conn, s)
        got = [roles[e["receiver"]] for e in h]
        blocks = len(system.ledger.blocks_for_serial(s))
        status = eng.verify_serial(s)["status"]
        ok = got == want and blocks == 4 and status == vf.RECORD_VERIFIED
        complete += ok
        case(exp, f"{s} Manufacturer->Distributor->Wholesaler->Pharmacy", "COMPLETE",
             "COMPLETE" if ok else f"INCOMPLETE roles={got} blocks={blocks} status={status}")
    metric(exp, "Traceability completeness (all four custody events recorded and verified)", complete, n,
           "fresh products moved through all four participants")
    conn.close()


# --------------------------------------------------------------------------- E3
def run_e3(system_cls, cfg):
    name = system_cls.name
    exp = f"E3 Access control [{name}]"
    rng = random.Random(SEED + 3)
    n_each = cfg["e3_each"]
    conn = new_db()
    system = system_cls(conn)
    groups = defaultdict(list)
    for op in make_ops(120 * 4):
        groups[op_serial(op)].append(op)
    for s, g in groups.items():                             # leave products at stage 0, 1 or 2
        for op in g[:1 + rng.choice([0, 1, 2])]:
            assert apply_op(system, op).ok
    serials = sorted(groups)
    roles, auth = roles_of(conn), authorized_ids(conn)

    def next_participant(owner):
        role = sc.ROLE_ORDER[sc.ROLE_ORDER.index(roles[owner]) + 1]
        return sorted(p for p in auth if roles[p] == role)[0]

    def state():
        return (conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                len(system.ledger.blocks) if system.ledger else 0,
                [tuple(r) for r in conn.execute("SELECT serial_number,current_owner,current_status "
                                                "FROM products ORDER BY 1")])

    ts = "2026-07-01 00:00:00"
    attempts = []
    batch = dict(drug_id="DRG001", product_name="Sample Antibiotic A", batch_number="BATCH2026A",
                 manufacturer="Manufacturer_A", manufacturing_date="2026-01-10", expiry_date="2028-01-10")
    for i in range(n_each):
        s = rng.choice(serials)
        owner = sc.get_product(conn, s)["current_owner"]
        nxt, ev = next_participant(owner), f"AUTH-{owner}-{s}"
        other = rng.choice([p for p in auth if p != owner])
        third = rng.choice([p for p in auth if p != other])
        attempts += [
            ("A unauthorized sender (Rogue_Actor)", sc.UNAUTHORIZED_SENDER, f"{s} Rogue_Actor->{nxt}",
             lambda s=s, nxt=nxt, ev=ev: system.transfer(s, "Rogue_Actor", nxt, ev, ts)),
            ("B unknown sender identity", sc.UNAUTHORIZED_SENDER, f"{s} Ghost_{i}->{nxt}",
             lambda s=s, nxt=nxt, ev=ev, i=i: system.transfer(s, f"Ghost_{i}", nxt, ev, ts)),
            ("C unauthorized receiver", sc.UNAUTHORIZED_RECEIVER, f"{s} {owner}->Rogue_Actor",
             lambda s=s, owner=owner, ev=ev: system.transfer(s, owner, "Rogue_Actor", ev, ts)),
            ("D sender is not the current holder", sc.INVALID_OWNERSHIP, f"{s} {other}->{third}",
             lambda s=s, other=other, third=third: system.transfer(s, other, third, f"AUTH-{other}-{s}", ts)),
            ("E missing authorization evidence", sc.MISSING_EVIDENCE, f"{s} {owner}->{nxt} evidence=''",
             lambda s=s, owner=owner, nxt=nxt: system.transfer(s, owner, nxt, "", ts)),
            ("F registration by non-manufacturer", sc.UNAUTHORIZED_REGISTRANT, f"SN7{i:05d}",
             lambda i=i: system.register(dict(batch, serial_number=f"SN7{i:05d}"),
                                         rng.choice(["Distributor_A", "Pharmacy_C", "Rogue_Actor", "Ghost"]), ts)),
        ]
    rejected_ok = unchanged = 0
    for kind, expected, label, fn in attempts:
        before = state()
        res = fn()
        same = state() == before
        unchanged += same
        rejected_ok += case(exp, f"{kind}: {label}", expected, res.code)
    metric(exp, "Unauthorized / invalid attempts rejected with the expected code", rejected_ok, len(attempts))
    metric(exp, "Database and ledger unchanged after a rejected attempt", unchanged, len(attempts))

    accepted = 0
    for i, s in enumerate(rng.sample(serials, n_each)):     # positive control: valid transfers pass
        owner = sc.get_product(conn, s)["current_owner"]
        nxt = next_participant(owner)
        res = system.transfer(s, owner, nxt, f"AUTH-{owner}-{s}",
                              (datetime(2026, 8, 1) + timedelta(seconds=i)).strftime(sc.FMT_TS))
        accepted += case(exp, f"valid transfer {s} {owner}->{nxt}", sc.OK, res.code)
    metric(exp, "Authorized, valid transfers accepted (no over-blocking)", accepted, n_each)
    conn.close()


# --------------------------------------------------------------------------- application world
def build_world(tmpdir):
    ctl = Controller(db_path=os.path.join(tmpdir, "app.db"), data_dir="dataset", logs_dir=LOG_DIR,
                     raw_db_path="pharma_traceability.db", reference_date=REF)
    load = ctl.load_synthetic_dataset()
    mem = sqlite3.connect(":memory:")
    ctl.conn.backup(mem)
    mem.row_factory = sqlite3.Row
    ctl.close()
    return mem, lg.Ledger(mem), load


def legit_serials():
    return sorted(s for s, sc_ in serial_ground_truth().items() if sc_ == "legitimate")


# --------------------------------------------------------------------------- E4
def run_e4(ledger, cfg):
    exp = "E4 Tamper detection (ledger)"
    rng = random.Random(SEED + 4)
    trials = cfg["trials"]
    head, length = ledger.anchor()
    ctrl_ok = 0
    for t in range(trials):
        r = lg.verify_blocks(fast_copy(ledger.blocks), head, length)
        ctrl_ok += case(exp, f"control (untouched ledger) #{t}", "INTACT", "INTACT" if r.ok else "FLAGGED")
    detected = {}
    for name, fn in TAMPERS:
        d = 0
        for t in range(trials):
            blocks = fast_copy(ledger.blocks)
            idx = fn(blocks, rng)
            r = lg.verify_blocks(blocks)
            d += case(exp, f"{name} #{t} (block {idx})", "DETECTED", "DETECTED" if not r.ok else "MISSED")
        detected[name] = d
        metric(exp, f"Detected: {name}", d, trials)
    tamper_total, detected_total = trials * len(TAMPERS), sum(detected.values())
    metric(exp, "Integrity verification rate (intact + modified ledgers classified correctly)",
           ctrl_ok + detected_total, trials + tamper_total,
           f"{trials} intact copies + {tamper_total} modified copies")
    metric(exp, "False alarms on intact ledgers", trials - ctrl_ok, trials)
    for name, fn in LIMITS:                                  # documented limitation scenarios
        chain_d = anchor_d = 0
        for t in range(trials):
            blocks = fast_copy(ledger.blocks)
            idx = fn(blocks, rng)
            chain_ok = lg.verify_blocks(blocks).ok
            anchor_ok = lg.verify_blocks(blocks, head, length).ok
            chain_d += not chain_ok
            anchor_d += not anchor_ok
            case(exp, f"{name} #{t} chain-only (known limitation)", "NOT DETECTED",
                 "NOT DETECTED" if chain_ok else "DETECTED")
            case(exp, f"{name} #{t} with external anchor", "DETECTED",
                 "DETECTED" if not anchor_ok else "MISSED")
        metric(exp, f"{name}: detected by chain alone", chain_d, trials, "known limitation of a hash chain")
        metric(exp, f"{name}: detected with external anchor", anchor_d, trials)


# --------------------------------------------------------------------------- E5, E6, E7
def raw_engine():
    raw = mem_copy("pharma_traceability.db")
    return raw, vf.VerificationEngine(raw, None, reference_date=REF)


def run_e5_e6_e7(mem, cfg):
    rng = random.Random(SEED + 5)
    legit = legit_serials()
    truth = serial_ground_truth()
    eng = vf.VerificationEngine(mem, None, reference_date=REF)          # rules only (no ledger)
    raw, raw_eng = raw_engine()

    # ---- E5 duplicates
    exp = "E5 Duplicate detection"
    d = 0
    for t in range(cfg["e5"]):
        s = rng.choice(legit)
        h = sc.get_history(mem, s)
        other = "Manufacturer_B" if h[0]["sender"] == "Manufacturer_A" else "Manufacturer_A"
        ts = (datetime.strptime(h[-1]["timestamp"], sc.FMT_TS) + timedelta(days=1)).strftime(sc.FMT_TS)
        mem.execute("INSERT INTO events (serial_number,event_type,sender,receiver,timestamp,"
                    "authorization_evidence) VALUES (?,?,?,?,?,?)",
                    (s, "REGISTER", other, other, ts, f"AUTH-{other}-{s}"))
        d += case(exp, f"inject second registration of {s}", vf.DUPLICATE,
                  actual_label(rules_of(eng.verify_serial(s)), vf.DUPLICATE))
        mem.rollback()
    metric(exp, "Duplicate registrations injected into legitimate products and detected", d, cfg["e5"])
    dup_serials = sorted(s for s, v in truth.items() if v == "duplicate_serial")
    dd = sum(case(exp, f"dataset duplicate serial {s}", vf.DUPLICATE,
                  actual_label(rules_of(raw_eng.verify_serial(s)), vf.DUPLICATE)) for s in dup_serials)
    metric(exp, "Dataset duplicate-serial cases detected (duplicate detection rate)", dd, len(dup_serials))

    # ---- E6 missing custody events
    exp = "E6 Missing custody events"
    by_cat = defaultdict(lambda: [0, 0])
    d = 0
    for t in range(cfg["e6"]):
        s = rng.choice(legit)
        h = sc.get_history(mem, s)
        idx = rng.randrange(len(h))
        cat = "first (registration)" if idx == 0 else ("last" if idx == len(h) - 1 else "middle")
        mem.execute("DELETE FROM events WHERE event_id = ?", (h[idx]["event_id"],))
        ok = case(exp, f"delete event {idx + 1}/{len(h)} of {s} [{cat}]", vf.TRACEABILITY,
                  actual_label(rules_of(eng.verify_serial(s)), vf.TRACEABILITY))
        d += ok
        by_cat[cat][0] += ok
        by_cat[cat][1] += 1
        mem.rollback()
    metric(exp, "Deleted custody events detected as traceability anomalies", d, cfg["e6"])
    for cat, (a, b) in sorted(by_cat.items()):
        metric(exp, f"  of which: deleted event position = {cat}", a, b)
    miss = sorted(s for s, v in truth.items() if v == "missing_custody_event")
    dm = sum(case(exp, f"dataset missing-event case {s}", vf.TRACEABILITY,
                  actual_label(rules_of(raw_eng.verify_serial(s)), vf.TRACEABILITY)) for s in miss)
    metric(exp, "Dataset missing-event cases detected", dm, len(miss))

    # ---- E7 data mismatch
    exp = "E7 Data mismatch"
    fields = ["drug_id", "product_name", "manufacturer", "manufacturing_date", "expiry_date", "batch_number"]
    per_field = max(1, cfg["e7"] // len(fields))
    new_value = {"drug_id": "DRG999", "product_name": "Altered Product", "manufacturing_date": "2025-05-05",
                 "expiry_date": "2031-01-01", "batch_number": "BATCH_FAKE"}
    total = d = 0
    for field in fields:
        fd = 0
        for t in range(per_field):
            s = rng.choice(legit)
            cur = sc.get_product(mem, s)
            new = new_value.get(field) or ("Manufacturer_B" if cur["manufacturer"] == "Manufacturer_A"
                                           else "Manufacturer_A")
            mem.execute(f"UPDATE products SET {field} = ? WHERE serial_number = ?", (new, s))
            fd += case(exp, f"change {field} of {s}", vf.MISMATCH,
                       actual_label(rules_of(eng.verify_serial(s)), vf.MISMATCH))
            mem.rollback()
        metric(exp, f"Mismatch detected when '{field}' is altered", fd, per_field)
        d += fd
        total += per_field
    metric(exp, "Altered product records detected (mismatch detection rate)", d, total)
    mm = sorted(s for s, v in truth.items() if v == "data_mismatch")
    dm = sum(case(exp, f"dataset mismatch case {s}", vf.MISMATCH,
                  actual_label(rules_of(raw_eng.verify_serial(s)), vf.MISMATCH)) for s in mm)
    metric(exp, "Dataset data-mismatch cases detected", dm, len(mm))
    raw.close()


# --------------------------------------------------------------------------- Step 11: tamper comparison
def run_tamper_comparison(mem, ledger, cfg):
    exp = "S11 Tamper comparison (database level)"
    rng = random.Random(SEED + 11)
    trials = cfg["trials"]
    legit = legit_serials()
    roles, auth = roles_of(mem), authorized_ids(mem)
    hist = {s: sc.get_history(mem, s) for s in legit}
    positions = defaultdict(list)
    for pos, b in enumerate(ledger.blocks):
        if b.data.get("serial_number"):
            positions[b.data["serial_number"]].append(pos)
    head, length = ledger.anchor()
    eng_base = vf.VerificationEngine(mem, None, reference_date=REF)
    eng_led = vf.VerificationEngine(mem, ledger, reference_date=REF)
    eng_anchor = vf.VerificationEngine(mem, ledger, reference_date=REF, expected_anchor=(head, length))

    def same_role_other(pid):
        return rng.choice([p for p in auth if roles[p] == roles[pid] and p != pid])

    def plus(ts, **kw):
        return (datetime.strptime(ts, sc.FMT_TS) + timedelta(**kw)).strftime(sc.FMT_TS)

    def tb1(s, h):   # edit receiver of a middle event
        mem.execute("UPDATE events SET receiver=? WHERE event_id=?", (same_role_other(h[1]["receiver"]), h[1]["event_id"]))

    def tb2(s, h):   # shift the timestamp of the newest event by one hour
        mem.execute("UPDATE events SET timestamp=? WHERE event_id=?", (plus(h[-1]["timestamp"], hours=1), h[-1]["event_id"]))

    def tb3(s, h):   # replace the authorization evidence
        mem.execute("UPDATE events SET authorization_evidence='AUTH-FORGED' WHERE event_id=?", (h[-1]["event_id"],))

    def tb4(s, h):   # delete a middle event
        mem.execute("DELETE FROM events WHERE event_id=?", (h[1]["event_id"],))

    def tb5(s, h):   # append a forged but CONSISTENT transfer and update the product record to match
        owner = h[-1]["receiver"]
        role = sc.ROLE_ORDER[sc.ROLE_ORDER.index(roles[owner]) + 1]
        new = rng.choice([p for p in auth if roles[p] == role])
        mem.execute("INSERT INTO events (serial_number,event_type,sender,receiver,timestamp,"
                    "authorization_evidence) VALUES (?,?,?,?,?,?)",
                    (s, "TRANSFER", owner, new, plus(h[-1]["timestamp"], days=1), f"AUTH-{owner}-{s}"))
        mem.execute("UPDATE products SET current_owner=?, current_status=? WHERE serial_number=?",
                    (new, sc.STATUS_BY_ROLE[role], s))

    def tb6(s, h):   # rewrite the newest event's receiver AND the product owner consistently
        new = same_role_other(h[-1]["receiver"])
        mem.execute("UPDATE events SET receiver=? WHERE event_id=?", (new, h[-1]["event_id"]))
        mem.execute("UPDATE products SET current_owner=? WHERE serial_number=?", (new, s))

    c_two = [s for s in legit if len(hist[s]) >= 2]
    c_three = [s for s in legit if len(hist[s]) >= 3]
    c_forge = [s for s in legit if roles[hist[s][-1]["receiver"]] != "Pharmacy"]
    scenarios = [
        ("TB1", "Edit a middle event (receiver changed)", c_three, tb1),
        ("TB2", "Shift an event timestamp (order still valid)", c_two, tb2),
        ("TB3", "Replace authorization evidence with another valid-looking value", c_two, tb3),
        ("TB4", "Delete a middle event", c_three, tb4),
        ("TB5", "Append a forged but consistent transfer (record updated to match)", c_forge, tb5),
        ("TB6", "Rewrite newest event and product owner consistently", c_two, tb6),
        ("TB7", "TB6 plus attacker also rewrites the ledger and recomputes every later hash", c_two, tb6),
    ]
    results = []
    rows = [("CONTROL", "No tampering (false-alarm check)", legit, None)] + scenarios
    for code, desc, cands, fn in rows:
        b = l = a = 0
        for t in range(trials):
            s = rng.choice(cands)
            e_base, e_led, e_anchor = eng_base, eng_led, eng_anchor
            if fn:
                fn(s, hist[s])
            if code == "TB7":
                blocks = fast_copy(ledger.blocks)
                pos = positions[s][-1]
                blocks[pos].data["receiver"] = sc.get_history(mem, s)[-1]["receiver"]   # the DB's new value
                for j in range(pos, len(blocks)):
                    if j > pos:
                        blocks[j].previous_hash = blocks[j - 1].hash
                    blocks[j].seal()
                forged = lg.Ledger()
                forged.blocks = blocks
                e_led = vf.VerificationEngine(mem, forged, reference_date=REF)
                e_anchor = vf.VerificationEngine(mem, forged, reference_date=REF, expected_anchor=(head, length))
            fb = bool(e_base.verify_serial(s)["findings"])
            fl = bool(e_led.verify_serial(s)["findings"])
            fa = bool(e_anchor.verify_serial(s)["findings"])
            mem.rollback()
            b, l, a = b + fb, l + fl, a + fa
            if code == "CONTROL":
                case(exp, f"{code} {s}", "NOT FLAGGED (baseline/ledger/anchor)",
                     "NOT FLAGGED (baseline/ledger/anchor)" if not (fb or fl or fa) else f"FLAGGED {fb}/{fl}/{fa}")
        results.append(dict(scenario=code, description=desc, trials=trials,
                            baseline_detected=b, baseline_rate=pct(b, trials),
                            ledger_detected=l, ledger_rate=pct(l, trials),
                            ledger_anchor_detected=a, ledger_anchor_rate=pct(a, trials)))
        if code == "CONTROL":
            metric(exp, "False alarms on untouched records (baseline | ledger | ledger+anchor)",
                   b + l + a, 3 * trials, f"{b} | {l} | {a} of {trials} each")
    return results


# --------------------------------------------------------------------------- Step 10 metrics
def completeness(conn):
    roles = roles_of(conn)
    done = total = 0
    for s, owner in [(r[0], r[1]) for r in conn.execute("SELECT serial_number, current_owner FROM products")]:
        h, seen, kept = sc.get_history(conn, s), False, 0
        for e in h:
            if e["event_type"] == "REGISTER":
                if seen:
                    continue
                seen = True
            kept += 1
        total += 1
        done += kept == sc.ROLE_ORDER.index(roles[owner]) + 1
    return done, total


def run_dataset_metrics(mem):
    exp = "S10 Dataset evaluation"
    truth = serial_ground_truth()
    raw, eng = raw_engine()
    rows, matrix = [], Counter()
    for s, scen in sorted(truth.items()):
        rep = eng.verify_serial(s)
        exp_status = EXPECTED_STATUS[scen]
        rows.append((scen, rep["flagged"], rep["status"] == exp_status))
        matrix[(exp_status, rep["status"])] += 1
        case(exp, f"{s} ({scen})", exp_status, rep["status"])
    anomalies = [r for r in rows if r[0] != "legitimate"]
    legit = [r for r in rows if r[0] == "legitimate"]
    tp, fp = sum(r[1] for r in anomalies), sum(r[1] for r in legit)
    fn, tn = len(anomalies) - tp, len(legit) - fp
    metric(exp, "Detection rate TP/(TP+FN)", tp, tp + fn, f"TP={tp} FN={fn}")
    metric(exp, "False-positive rate FP/(FP+TN)", fp, fp + tn, f"FP={fp} TN={tn}")
    metric(exp, "Overall multi-class accuracy (status = expected status)", sum(r[2] for r in rows), len(rows))
    for scen in EXPECTED_STATUS:
        g = [r for r in rows if r[0] == scen]
        metric(exp, f"  detection / correct status: {scen}", sum(r[2] for r in g), len(g))
    labels = [vf.RECORD_VERIFIED] + [s for s in vf.PRIORITY if s != vf.BLOCKCHAIN]
    with open(f"{LOG_DIR}/phase6_confusion_matrix.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["expected \\ actual"] + labels)
        for e in labels:
            w.writerow([e] + [matrix[(e, a)] for a in labels])
    for label, conn in (("raw dataset (faults present)", raw), ("application database (rules enforced at entry)", mem)):
        c, t = completeness(conn)
        metric("S10 Traceability completeness", f"Products with all expected custody events: {label}", c, t,
               "events recorded = stages from manufacturer to current holder")
    raw.close()
    return labels, matrix


# --------------------------------------------------------------------------- E8 performance
def perf_run(system_cls, ops, tmpdir, qrng, samples):
    path = os.path.join(tmpdir, f"perf_{system_cls.name}.db")
    if os.path.exists(path):
        os.remove(path)
    conn = new_db(path)
    system = system_cls(conn)
    lat, accepted = [], 0
    t_all = time.perf_counter()
    for op in ops:
        t = time.perf_counter()
        res = apply_op(system, op)
        lat.append(time.perf_counter() - t)
        accepted += res.ok
    total = time.perf_counter() - t_all
    serials = qrng.sample(sorted({op_serial(o) for o in ops}), min(samples, len({op_serial(o) for o in ops})))
    q_sql, q_scan = [], []
    for s in serials:
        t = time.perf_counter()
        system.history(s)
        q_sql.append(time.perf_counter() - t)
        if system.ledger:
            t = time.perf_counter()
            system.ledger.blocks_for_serial(s)
            q_scan.append(time.perf_counter() - t)
    verify_ms, payload = "", ""
    chain_ok = True
    if system.ledger:
        t = time.perf_counter()
        chain_ok = system.ledger.verify_chain().ok
        verify_ms = (time.perf_counter() - t) * 1000
        payload = conn.execute("SELECT SUM(LENGTH(data)+LENGTH(hash)+LENGTH(previous_hash)+LENGTH(timestamp)) "
                               "FROM ledger_blocks").fetchone()[0]
    conn.commit()
    conn.close()
    lat_sorted = sorted(lat)
    return dict(system=system_cls.name, n=len(ops), accepted=accepted, chain_ok=chain_ok,
                mean_latency_ms=statistics.mean(lat) * 1000,
                p95_latency_ms=lat_sorted[int(0.95 * (len(lat_sorted) - 1))] * 1000,
                throughput_eps=len(ops) / total,
                history_sql_ms=statistics.mean(q_sql) * 1000,
                history_ledger_scan_ms=(statistics.mean(q_scan) * 1000) if q_scan else "",
                ledger_verify_ms=verify_ms, db_size_kb=os.path.getsize(path) / 1024,
                ledger_payload_kb=(payload / 1024) if payload != "" else "")


def run_e8(cfg, tmpdir):
    exp = "E8 Performance"
    qrng = random.Random(SEED + 8)
    ops_by_n = {n: make_ops(n) for n in cfg["sizes"]}
    runs = []
    for rep in range(cfg["repeats"]):
        for n in cfg["sizes"]:
            order = [SQLiteBaseline, Blockchain] if rep % 2 == 0 else [Blockchain, SQLiteBaseline]
            for cls in order:
                r = perf_run(cls, ops_by_n[n], tmpdir, qrng, cfg["query_samples"])
                r["run"] = rep + 1
                runs.append(r)
                case(exp, f"{cls.name} N={n} run {rep + 1}",
                     "ALL EVENTS ACCEPTED" + (" AND LEDGER VALID" if cls is Blockchain else ""),
                     ("ALL EVENTS ACCEPTED" if r["accepted"] == n else f"ACCEPTED {r['accepted']}/{n}")
                     + (" AND LEDGER VALID" if cls is Blockchain and r["chain_ok"] else
                        (" AND LEDGER INVALID" if cls is Blockchain else "")))
        print(f"  performance repeat {rep + 1}/{cfg['repeats']} done", flush=True)
    return runs


def sd(xs):
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def summarize_perf(runs, sizes):
    out = []
    for n in sizes:
        for system in ("baseline", "blockchain"):
            rs = [r for r in runs if r["n"] == n and r["system"] == system]
            col = lambda k: [r[k] for r in rs if r[k] != ""]
            row = dict(n_events=n, system=system, runs=len(rs),
                       latency_ms_mean=statistics.mean(col("mean_latency_ms")),
                       latency_ms_std=sd(col("mean_latency_ms")),
                       latency_ms_p95=statistics.mean(col("p95_latency_ms")),
                       throughput_eps_mean=statistics.mean(col("throughput_eps")),
                       throughput_eps_std=sd(col("throughput_eps")),
                       history_sql_ms=statistics.mean(col("history_sql_ms")),
                       history_ledger_scan_ms=statistics.mean(col("history_ledger_scan_ms")) if col("history_ledger_scan_ms") else "",
                       ledger_verify_ms=statistics.mean(col("ledger_verify_ms")) if col("ledger_verify_ms") else "",
                       db_size_kb=statistics.mean(col("db_size_kb")),
                       ledger_payload_kb=statistics.mean(col("ledger_payload_kb")) if col("ledger_payload_kb") else "")
            out.append(row)
    comp = []
    for n in sizes:
        b = next(r for r in out if r["n_events"] == n and r["system"] == "baseline")
        c = next(r for r in out if r["n_events"] == n and r["system"] == "blockchain")
        comp.append(dict(
            n_events=n,
            baseline_latency_ms=b["latency_ms_mean"], blockchain_latency_ms=c["latency_ms_mean"],
            latency_overhead_pct=100 * (c["latency_ms_mean"] / b["latency_ms_mean"] - 1),
            baseline_throughput_eps=b["throughput_eps_mean"], blockchain_throughput_eps=c["throughput_eps_mean"],
            throughput_change_pct=100 * (c["throughput_eps_mean"] / b["throughput_eps_mean"] - 1),
            history_sql_ms_baseline=b["history_sql_ms"], history_sql_ms_blockchain=c["history_sql_ms"],
            history_ledger_scan_ms=c["history_ledger_scan_ms"],
            baseline_db_kb=b["db_size_kb"], blockchain_db_kb=c["db_size_kb"],
            storage_overhead_pct=100 * (c["db_size_kb"] / b["db_size_kb"] - 1),
            ledger_verify_ms=c["ledger_verify_ms"]))
    return out, comp


# --------------------------------------------------------------------------- output
def write_csv(path, rows, fields=None):
    fields = fields or list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})


def write_environment(cfg, quick):
    import matplotlib
    lines = [f"run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", f"mode: {'QUICK' if quick else 'FULL'}",
             f"python: {sys.version.split()[0]}", f"platform: {platform.platform()}",
             f"cpu count: {os.cpu_count()}", f"sqlite: {sqlite3.sqlite_version}",
             f"matplotlib: {matplotlib.__version__}", f"seed: {SEED}",
             f"settings: {cfg}", "sqlite synchronous=FULL (default): every event is committed (durable) before the next",
             "timings are wall-clock on the machine above and vary between machines and runs"]
    with open(f"{LOG_DIR}/phase6_environment.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    global LOG_DIR, FIG_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="small trial counts (script check only)")
    ap.add_argument("--out", default=".", help="folder for logs/ and figures/ (default: current folder)")
    args = ap.parse_args()
    cfg = QUICK if args.quick else FULL
    LOG_DIR, FIG_DIR = os.path.join(args.out, "logs"), os.path.join(args.out, "figures")
    for d in (LOG_DIR, FIG_DIR):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists("pharma_traceability.db") or not os.path.exists("dataset/events.csv"):
        sys.exit("Run generate_dataset.py first")
    tmpdir = tempfile.mkdtemp(prefix="phase6_")
    t0 = time.time()
    write_environment(cfg, args.quick)

    def step(msg):
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)

    step("E1 product registration")
    run_e1()
    step("E2 traceability")
    run_e2(cfg)
    step("E3 access control (blockchain + baseline)")
    run_e3(Blockchain, cfg)
    run_e3(SQLiteBaseline, cfg)
    step("building application database + ledger from the dataset")
    mem, ledger, load = build_world(tmpdir)
    print(f"  accepted {load['accepted']}, rejected {load['rejected']}, blocks {load['blocks']}", flush=True)
    step("E4 ledger tamper detection")
    run_e4(ledger, cfg)
    step("E5 / E6 / E7 mutation experiments")
    run_e5_e6_e7(mem, cfg)
    step("Step 11 database-level tamper comparison")
    tamper = run_tamper_comparison(mem, ledger, cfg)
    step("Step 10 dataset metrics")
    labels, matrix = run_dataset_metrics(mem)
    step("E8 performance")
    runs = run_e8(cfg, tmpdir)
    perf, comp = summarize_perf(runs, cfg["sizes"])

    write_csv(f"{LOG_DIR}/phase6_test_cases.csv", TEST_ROWS)
    summ = []
    for exp in dict.fromkeys(r["experiment"] for r in TEST_ROWS):
        rs = [r for r in TEST_ROWS if r["experiment"] == exp]
        p = sum(r["status"] == "PASS" for r in rs)
        summ.append(dict(experiment=exp, tests=len(rs), passed=p, failed=len(rs) - p, pass_rate=pct(p, len(rs))))
    write_csv(f"{LOG_DIR}/phase6_experiment_summary.csv", summ)
    write_csv(f"{LOG_DIR}/phase6_metrics.csv", METRICS)
    write_csv(f"{LOG_DIR}/phase6_tamper_comparison.csv", tamper)
    write_csv(f"{LOG_DIR}/phase6_performance_runs.csv", runs)
    write_csv(f"{LOG_DIR}/phase6_performance_summary.csv", perf)
    write_csv(f"{LOG_DIR}/phase6_baseline_comparison.csv", comp)

    step("figures")
    import figures
    figures.make_all(perf, comp, tamper, labels, matrix, FIG_DIR)

    print("\n=== EXPERIMENT SUMMARY ===")
    for r in summ:
        print(f"  {r['experiment']:45s} {r['passed']:5d}/{r['tests']:<5d} {r['pass_rate']:>7s}")
    print("\n=== KEY METRICS ===")
    for m in METRICS:
        if not m["measure"].startswith("  "):
            print(f"  [{m['experiment'][:34]:34s}] {m['measure'][:78]:78s} {m['successes']}/{m['n']} = {m['rate']}")
    print("\n=== TAMPER COMPARISON (detected of trials) ===")
    for r in tamper:
        print(f"  {r['scenario']:8s} baseline {r['baseline_rate']:>6s} | ledger {r['ledger_rate']:>6s} | "
              f"ledger+anchor {r['ledger_anchor_rate']:>6s}  {r['description']}")
    print("\n=== PERFORMANCE (mean over runs) ===")
    for r in comp:
        print(f"  N={r['n_events']:5d} latency {r['baseline_latency_ms']:.3f} -> {r['blockchain_latency_ms']:.3f} ms "
              f"({r['latency_overhead_pct']:+.0f}%) | throughput {r['baseline_throughput_eps']:.0f} -> "
              f"{r['blockchain_throughput_eps']:.0f} ev/s ({r['throughput_change_pct']:+.0f}%) | "
              f"db {r['baseline_db_kb']:.0f} -> {r['blockchain_db_kb']:.0f} KB ({r['storage_overhead_pct']:+.0f}%)")
    unexpected = [r for r in TEST_ROWS if r["status"] != "PASS"]
    print(f"\nTotal test cases: {len(TEST_ROWS)} | failed: {len(unexpected)} | elapsed {time.time() - t0:.0f}s")
    print("RESULT:", "ALL TEST CASES PASSED" if not unexpected else "FAILURES PRESENT")
    return 0 if not unexpected else 1


if __name__ == "__main__":
    sys.exit(main())
