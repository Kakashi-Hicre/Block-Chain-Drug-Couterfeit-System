"""
generate_dataset.py - Phase 1: build the SYNTHETIC pharmaceutical dataset.

Produces (in ./dataset/):
    participants.csv   authorized supply-chain actors (+ one unauthorized actor)
    batches.csv        master record of each batch (used to detect data mismatches)
    products.csv       registration records (some deliberately manipulated)
    events.csv         custody events (REGISTER / TRANSFER)
    test_cases.csv     ground truth: one row per test package with expected result

and loads everything into the SQLite database pharma_traceability.db.

ALL DATA IS SYNTHETIC. Product names are fictional.
Run:  python generate_dataset.py
"""
import csv
import os
import random
from collections import Counter
from datetime import date, datetime, timedelta

from database import DB_PATH, reset_database

SEED = 42
OUT_DIR = "dataset"
REFERENCE_DATE = date(2026, 6, 1)   # "today" used to decide whether a product is expired

# Number of test packages per scenario (total = 1000)
SCENARIO_COUNTS = {
    "legitimate": 800,
    "duplicate_serial": 40,
    "unregistered_product": 40,
    "unauthorized_transfer": 40,
    "expired_product": 30,
    "missing_custody_event": 30,
    "data_mismatch": 20,
}

EXPECTED_RESULT = {
    "legitimate": "RECORD_VERIFIED",
    "duplicate_serial": "DUPLICATE_SERIAL_ALERT",
    "unregistered_product": "UNREGISTERED_PRODUCT_ALERT",
    "unauthorized_transfer": "UNAUTHORIZED_TRANSFER_ALERT",
    "expired_product": "EXPIRY_WARNING",
    "missing_custody_event": "TRACEABILITY_WARNING",
    "data_mismatch": "DATA_MISMATCH_ALERT",
}

PARTICIPANTS = [
    ("Manufacturer_A", "Manufacturer", 1), ("Manufacturer_B", "Manufacturer", 1),
    ("Distributor_A", "Distributor", 1), ("Distributor_B", "Distributor", 1),
    ("Wholesaler_A", "Wholesaler", 1), ("Wholesaler_B", "Wholesaler", 1),
    ("Pharmacy_A", "Pharmacy", 1), ("Pharmacy_B", "Pharmacy", 1), ("Pharmacy_C", "Pharmacy", 1),
    ("Rogue_Actor", "Unknown", 0),   # NOT authorized: used for unauthorized-transfer cases
]
BY_ROLE = {}
for pid, role, auth in PARTICIPANTS:
    if auth:
        BY_ROLE.setdefault(role, []).append(pid)

DRUGS = [  # fictional names only
    ("DRG001", "Sample Antibiotic A"), ("DRG002", "Sample Painkiller B"),
    ("DRG003", "Sample Antimalarial C"), ("DRG004", "Sample Antiviral D"),
    ("DRG005", "Sample Antacid E"), ("DRG006", "Sample Vitamin F"),
    ("DRG007", "Sample Antihistamine G"), ("DRG008", "Sample Antidiabetic H"),
    ("DRG009", "Sample Antihypertensive I"), ("DRG010", "Sample Cough Syrup J"),
]

# Chain stages: how far a package has travelled
STAGES = ["Registered", "In distribution", "At wholesaler", "At pharmacy"]
STAGE_ROLES = ["Manufacturer", "Distributor", "Wholesaler", "Pharmacy"]

FMT_TS = "%Y-%m-%d %H:%M:%S"


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + years, day=28)


def make_batches():
    """16 valid batches (expiry after REFERENCE_DATE) + 4 already-expired batches."""
    batches = []
    for i in range(16):
        drug_id, name = DRUGS[i % len(DRUGS)]
        mfg = date(2026, 1, 10) + timedelta(days=5 * i)
        batches.append({
            "batch_number": f"BATCH2026{chr(65 + i)}", "drug_id": drug_id, "product_name": name,
            "manufacturer": "Manufacturer_A" if i % 2 == 0 else "Manufacturer_B",
            "manufacturing_date": mfg.isoformat(), "expiry_date": add_years(mfg, 2).isoformat(),
        })
    for i in range(4):
        drug_id, name = DRUGS[(i + 3) % len(DRUGS)]
        batches.append({
            "batch_number": f"BATCHOLD{i + 1}", "drug_id": drug_id, "product_name": name,
            "manufacturer": "Manufacturer_A" if i % 2 == 0 else "Manufacturer_B",
            "manufacturing_date": date(2024, 3, 1 + 5 * i).isoformat(),
            "expiry_date": date(2026, 3, 1 + 5 * i).isoformat(),   # before REFERENCE_DATE
        })
    return batches


def ts(dt):
    return dt.strftime(FMT_TS)


def evidence(sender, serial):
    return f"AUTH-{sender}-{serial}"


def make_events(rec_id, serial, manufacturer, stage, rng, start=None, skip=None):
    """
    Build the custody events for one package up to `stage` (0..3).
    skip = None | 'wholesaler' | 'first_transfer'  -> deliberately omit an event.
    Returns (events, owner, status).
    """
    t = start or datetime(2026, 1, 15) + timedelta(days=rng.randint(0, 100), seconds=rng.randint(0, 86399))
    events = [dict(record_id=rec_id, serial_number=serial, event_type="REGISTER",
                   sender=manufacturer, receiver=manufacturer, timestamp=ts(t),
                   authorization_evidence=evidence(manufacturer, serial))]
    owner = manufacturer
    for s in range(1, stage + 1):
        receiver = rng.choice(BY_ROLE[STAGE_ROLES[s]])
        if skip == "wholesaler" and s == 2:
            continue                      # wholesaler step omitted; next sender is the distributor
        if skip == "first_transfer" and s == 1:
            continue                      # manufacturer->distributor omitted
        sender = owner if not skip else (owner if skip == "wholesaler" else rng.choice(BY_ROLE["Distributor"]))
        if skip == "wholesaler" and s == 3:
            sender = owner                # distributor sends straight to pharmacy
        t += timedelta(days=rng.randint(1, 10), seconds=rng.randint(0, 86399))
        events.append(dict(record_id=rec_id, serial_number=serial, event_type="TRANSFER",
                           sender=sender, receiver=receiver, timestamp=ts(t),
                           authorization_evidence=evidence(sender, serial)))
        owner = receiver
    return events, owner, STAGES[stage]


def product_row(rec_id, serial, batch, owner, status, **override):
    row = dict(record_id=rec_id, serial_number=serial, drug_id=batch["drug_id"],
               product_name=batch["product_name"], batch_number=batch["batch_number"],
               manufacturer=batch["manufacturer"], manufacturing_date=batch["manufacturing_date"],
               expiry_date=batch["expiry_date"], current_owner=owner, current_status=status,
               is_synthetic=1)
    row.update(override)
    return row


def generate():
    rng = random.Random(SEED)
    batches = make_batches()
    valid_batches = batches[:16]
    expired_batches = batches[16:]

    scenarios = [s for s, n in SCENARIO_COUNTS.items() for _ in range(n)]
    rng.shuffle(scenarios)

    products, events, cases = [], [], []
    serial_counter, unreg_counter = 0, 0
    legit_pool = []          # (record_id, product_row, first_event_ts) usable by duplicate cases
    pending_duplicates = []  # (index, record_id)

    for idx, scenario in enumerate(scenarios, start=1):
        rec_id = f"R{idx:04d}"

        if scenario == "duplicate_serial":
            pending_duplicates.append(rec_id)
            cases.append(dict(record_id=rec_id, serial_number="", scenario=scenario))
            continue

        if scenario == "unregistered_product":
            unreg_counter += 1
            serial = f"SN9{unreg_counter:05d}"
            t = datetime(2026, 2, 1) + timedelta(days=rng.randint(0, 90), seconds=rng.randint(0, 86399))
            sender = rng.choice(BY_ROLE["Distributor"])
            events.append(dict(record_id=rec_id, serial_number=serial, event_type="TRANSFER",
                               sender=sender, receiver=rng.choice(BY_ROLE["Wholesaler"]),
                               timestamp=ts(t), authorization_evidence=evidence(sender, serial)))
            cases.append(dict(record_id=rec_id, serial_number=serial, scenario=scenario))
            continue

        serial_counter += 1
        serial = f"SN{serial_counter:06d}"

        if scenario == "legitimate":
            batch = rng.choice(valid_batches)
            stage = rng.choices([0, 1, 2, 3], weights=[10, 20, 20, 50])[0]
            ev, owner, status = make_events(rec_id, serial, batch["manufacturer"], stage, rng)
            products.append(product_row(rec_id, serial, batch, owner, status))
            events += ev
            legit_pool.append((rec_id, products[-1], ev[0]["timestamp"]))

        elif scenario == "expired_product":
            batch = rng.choice(expired_batches)
            stage = rng.choices([1, 2, 3], weights=[20, 30, 50])[0]
            ev, owner, status = make_events(rec_id, serial, batch["manufacturer"], stage, rng)
            products.append(product_row(rec_id, serial, batch, owner, status))
            events += ev

        elif scenario == "unauthorized_transfer":
            batch = rng.choice(valid_batches)
            ev, owner, status = make_events(rec_id, serial, batch["manufacturer"], 1, rng)
            t = datetime.strptime(ev[-1]["timestamp"], FMT_TS) + timedelta(days=rng.randint(1, 8))
            receiver = rng.choice(BY_ROLE["Pharmacy"])
            ev.append(dict(record_id=rec_id, serial_number=serial, event_type="TRANSFER",
                           sender="Rogue_Actor", receiver=receiver, timestamp=ts(t),
                           authorization_evidence="NONE"))
            products.append(product_row(rec_id, serial, batch, receiver, "At pharmacy"))
            events += ev

        elif scenario == "missing_custody_event":
            batch = rng.choice(valid_batches)
            if rng.random() < 0.6:     # wholesaler step missing: Distributor -> Pharmacy directly
                ev, owner, status = make_events(rec_id, serial, batch["manufacturer"], 3, rng, skip="wholesaler")
            else:                      # first transfer missing: Distributor appears with no prior custody
                ev, owner, status = make_events(rec_id, serial, batch["manufacturer"], 2, rng, skip="first_transfer")
            products.append(product_row(rec_id, serial, batch, owner, status))
            events += ev

        elif scenario == "data_mismatch":
            batch = rng.choice(valid_batches)
            ev, owner, status = make_events(rec_id, serial, batch["manufacturer"], 2, rng)
            if rng.random() < 0.5:     # wrong manufacturer for this batch
                wrong = "Manufacturer_B" if batch["manufacturer"] == "Manufacturer_A" else "Manufacturer_A"
                products.append(product_row(rec_id, serial, batch, owner, status, manufacturer=wrong))
            else:                      # expiry date altered (extended by 1 year)
                new_exp = add_years(date.fromisoformat(batch["expiry_date"]), 1).isoformat()
                products.append(product_row(rec_id, serial, batch, owner, status, expiry_date=new_exp))
            events += ev

        cases.append(dict(record_id=rec_id, serial_number=serial, scenario=scenario))

    # Duplicate-serial cases: re-use the serial of an existing legitimate product
    # each duplicate targets a DIFFERENT original (sampled without replacement)
    targets = rng.sample(legit_pool, len(pending_duplicates))
    for rec_id, (orig_rec, orig_row, orig_ts) in zip(pending_duplicates, targets):
        batch = next(b for b in batches if b["batch_number"] == orig_row["batch_number"])
        other_mfr = "Manufacturer_B" if batch["manufacturer"] == "Manufacturer_A" else "Manufacturer_A"
        t = datetime.strptime(orig_ts, FMT_TS) + timedelta(days=rng.randint(20, 60), seconds=rng.randint(0, 86399))
        serial = orig_row["serial_number"]
        owner = rng.choice(BY_ROLE["Pharmacy"])
        products.append(product_row(rec_id, serial, batch, owner, "At pharmacy", manufacturer=other_mfr))
        events.append(dict(record_id=rec_id, serial_number=serial, event_type="REGISTER",
                           sender=other_mfr, receiver=other_mfr, timestamp=ts(t),
                           authorization_evidence=evidence(other_mfr, serial)))
        for c in cases:
            if c["record_id"] == rec_id:
                c["serial_number"] = serial
                c["duplicates_record"] = orig_rec

    for c in cases:
        c["expected_result"] = EXPECTED_RESULT[c["scenario"]]
        c["is_anomaly"] = 0 if c["scenario"] == "legitimate" else 1
        c.setdefault("duplicates_record", "")

    products.sort(key=lambda r: r["record_id"])
    events.sort(key=lambda r: (r["timestamp"], r["record_id"]))
    cases.sort(key=lambda r: r["record_id"])
    return batches, products, events, cases


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def load_into_db(batches, products, events):
    conn = reset_database(DB_PATH)
    conn.executemany("INSERT INTO participants VALUES (?,?,?)", PARTICIPANTS)
    conn.executemany(
        "INSERT INTO batches VALUES (:batch_number,:drug_id,:product_name,:manufacturer,"
        ":manufacturing_date,:expiry_date)", batches)
    # Duplicate serials cannot enter the products table (PRIMARY KEY): they are replayed
    # through register_product() in Phase 2, where the duplicate check must reject them.
    # Insert in REGISTRATION-TIME order so that, for a re-used serial number, the FIRST registration
    # (the original) is the one kept - the same "first registration wins" rule the application uses.
    reg_time = {e["record_id"]: e["timestamp"] for e in events if e["event_type"] == "REGISTER"}
    skipped = 0
    for p in sorted(products, key=lambda r: (reg_time[r["record_id"]], r["record_id"])):
        cur = conn.execute(
            "INSERT OR IGNORE INTO products VALUES (:serial_number,:drug_id,:product_name,"
            ":batch_number,:manufacturer,:manufacturing_date,:expiry_date,:current_owner,"
            ":current_status,:is_synthetic)", p)
        skipped += 1 - cur.rowcount
    conn.executemany(
        "INSERT INTO events (serial_number,event_type,sender,receiver,timestamp,authorization_evidence) "
        "VALUES (:serial_number,:event_type,:sender,:receiver,:timestamp,:authorization_evidence)", events)
    conn.commit()
    n_prod = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    n_ev = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    return n_prod, n_ev, skipped


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    batches, products, events, cases = generate()

    write_csv(f"{OUT_DIR}/participants.csv",
              [dict(participant_id=p, role=r, authorized=a) for p, r, a in PARTICIPANTS],
              ["participant_id", "role", "authorized"])
    write_csv(f"{OUT_DIR}/batches.csv", batches,
              ["batch_number", "drug_id", "product_name", "manufacturer",
               "manufacturing_date", "expiry_date"])
    write_csv(f"{OUT_DIR}/products.csv", products,
              ["record_id", "serial_number", "drug_id", "product_name", "batch_number",
               "manufacturer", "manufacturing_date", "expiry_date", "current_owner",
               "current_status", "is_synthetic"])
    write_csv(f"{OUT_DIR}/events.csv", events,
              ["record_id", "serial_number", "event_type", "sender", "receiver",
               "timestamp", "authorization_evidence"])
    write_csv(f"{OUT_DIR}/test_cases.csv", cases,
              ["record_id", "serial_number", "scenario", "expected_result",
               "is_anomaly", "duplicates_record"])

    n_prod, n_ev, skipped = load_into_db(batches, products, events)

    print("SYNTHETIC dataset generated (seed =", SEED, ")")
    print("Test packages:", len(cases))
    for s, n in Counter(c["scenario"] for c in cases).items():
        print(f"  {s:24s} {n}")
    print(f"products.csv rows: {len(products)} | events.csv rows: {len(events)}")
    print(f"Loaded into {DB_PATH}: {n_prod} products, {n_ev} events "
          f"({skipped} duplicate-serial rows kept out of the products table)")


if __name__ == "__main__":
    main()
