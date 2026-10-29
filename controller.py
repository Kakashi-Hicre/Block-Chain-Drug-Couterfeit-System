"""
controller.py - Phase 5: application logic behind the Tkinter interface (no GUI code here,
so everything can be tested without a display).

All data is SYNTHETIC.

The controller owns the application database (separate from the Phase 1 dataset database),
the hash-linked ledger, and a log of REJECTED operations. Accepted events go to the database
and the ledger; rejected register/transfer attempts are logged (never put on the ledger) so
they can be reviewed as suspicious-attempt alerts.
"""
import copy
import csv
import glob
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import date, datetime

import database
import ledger as lg
import supply_chain as sc
import verifier as vf
from generate_dataset import PARTICIPANTS

REJECTED_SCHEMA = """
CREATE TABLE IF NOT EXISTS rejected_attempts (
    attempt_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    kind          TEXT NOT NULL,           -- REGISTER / TRANSFER
    serial_number TEXT,
    actor         TEXT,
    detail        TEXT,
    code          TEXT NOT NULL,
    message       TEXT NOT NULL
);
"""
ALL_TABLES = ("ledger_blocks", "rejected_attempts", "events", "products", "batches", "participants")


class Controller:
    def __init__(self, db_path="pharma_app.db", data_dir="dataset", logs_dir="logs",
                 raw_db_path="pharma_traceability.db", reference_date=None):
        self.db_path = db_path
        self.data_dir = data_dir
        self.logs_dir = logs_dir
        self.raw_db_path = raw_db_path
        self.reference_date = reference_date or date.today()
        self.anchor_path = db_path + ".anchor.json"      # stored OUTSIDE the database on purpose
        self.conn = database.get_connection(db_path)
        self._init_schema()
        self.ledger = lg.Ledger(self.conn)

    # ------------------------------------------------------------------ setup
    def _init_schema(self):
        database.create_schema(self.conn)
        self.conn.executescript(REJECTED_SCHEMA)
        if self.conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 0:
            self.conn.executemany("INSERT INTO participants VALUES (?,?,?)", PARTICIPANTS)
        if self.conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0:
            path = os.path.join(self.data_dir, "batches.csv")
            if os.path.exists(path):
                with open(path, newline="", encoding="utf-8") as f:
                    self.conn.executemany(
                        "INSERT INTO batches VALUES (:batch_number,:drug_id,:product_name,:manufacturer,"
                        ":manufacturing_date,:expiry_date)", list(csv.DictReader(f)))
        self.conn.commit()

    def reset(self):
        """Erase all application data (products, events, ledger, rejected attempts)."""
        for t in ALL_TABLES:
            self.conn.execute(f"DROP TABLE IF EXISTS {t}")
        self.conn.commit()
        if os.path.exists(self.anchor_path):
            os.remove(self.anchor_path)
        self._init_schema()
        self.ledger = lg.Ledger(self.conn)

    def close(self):
        self.conn.close()

    # --------------------------------------------------------------- reference data
    def participant_ids(self, role=None, authorized_only=False):
        q, args = "SELECT participant_id FROM participants", []
        cond = []
        if role:
            cond.append("role = ?")
            args.append(role)
        if authorized_only:
            cond.append("authorized = 1")
        if cond:
            q += " WHERE " + " AND ".join(cond)
        return [r[0] for r in self.conn.execute(q + " ORDER BY participant_id", args)]

    def batches(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM batches ORDER BY batch_number")]

    def add_batch(self, batch):
        """Add a NEW batch master record (so own data can be verified). Existing batches are never changed."""
        keys = ["batch_number", "drug_id", "product_name", "manufacturer", "manufacturing_date", "expiry_date"]
        missing = [k for k in keys if not str(batch.get(k, "")).strip()]
        if missing:
            return sc.Result(False, sc.MISSING_FIELD, f"Missing required field(s): {', '.join(missing)}")
        batch = {k: str(batch[k]).strip() for k in keys}
        mfg, exp = sc._parse(batch["manufacturing_date"], sc.FMT_DATE), sc._parse(batch["expiry_date"], sc.FMT_DATE)
        if mfg is None or exp is None or exp <= mfg:
            return sc.Result(False, sc.INVALID_DATE, "Dates must be YYYY-MM-DD and expiry must be after manufacturing")
        p = sc.get_participant(self.conn, batch["manufacturer"])
        if p is None or not p["authorized"] or p["role"] != "Manufacturer":
            return sc.Result(False, sc.UNAUTHORIZED_REGISTRANT,
                             f"{batch['manufacturer']} is not an authorized manufacturer (use e.g. Manufacturer_A)")
        if self.conn.execute("SELECT 1 FROM batches WHERE batch_number=?", (batch["batch_number"],)).fetchone():
            return sc.Result(False, sc.DUPLICATE_SERIAL, f"Batch {batch['batch_number']} already exists")
        self.conn.execute("INSERT INTO batches VALUES (:batch_number,:drug_id,:product_name,:manufacturer,"
                          ":manufacturing_date,:expiry_date)", batch)
        self.conn.commit()
        return sc.Result(True, sc.OK, f"Batch {batch['batch_number']} added to the batch master record")

    def next_serial(self):
        nums = [int(m.group(1)) for (s,) in self.conn.execute("SELECT serial_number FROM products")
                if (m := re.fullmatch(r"SN(\d{6})", s)) and int(m.group(1)) < 900000]
        return f"SN{(max(nums) + 1) if nums else 1:06d}"

    # ------------------------------------------------------------------ operations
    def register(self, product, registrant, timestamp=None, evidence=None):
        res = sc.register_product(self.conn, product, registrant, timestamp, evidence, ledger=self.ledger)
        if not res.ok:
            self._log_rejected("REGISTER", str(product.get("serial_number", "")).strip(), registrant,
                               f"{product.get('product_name', '')} / {product.get('batch_number', '')}",
                               res, timestamp)
        return res

    def transfer(self, serial, sender, receiver, evidence, timestamp=None):
        res = sc.transfer_product(self.conn, serial, sender, receiver, evidence, timestamp,
                                  ledger=self.ledger)
        if not res.ok:
            self._log_rejected("TRANSFER", serial, sender, f"{sender} -> {receiver}", res, timestamp)
        return res

    def _log_rejected(self, kind, serial, actor, detail, res, timestamp=None):
        self.conn.execute(
            "INSERT INTO rejected_attempts (timestamp, kind, serial_number, actor, detail, code, message) "
            "VALUES (?,?,?,?,?,?,?)",
            (timestamp or datetime.now().strftime(sc.FMT_TS), kind, serial, actor, detail,
             res.code, res.message))
        self.conn.commit()

    # --------------------------------------------------------------------- queries
    def products(self, text=""):
        q = "SELECT * FROM products"
        args = []
        if text.strip():
            like = f"%{text.strip()}%"
            q += (" WHERE serial_number LIKE ? OR product_name LIKE ? OR batch_number LIKE ? "
                  "OR current_owner LIKE ? OR current_status LIKE ? OR manufacturer LIKE ?")
            args = [like] * 6
        return [dict(r) for r in self.conn.execute(q + " ORDER BY serial_number", args)]

    def product(self, serial):
        r = sc.get_product(self.conn, serial.strip())
        return dict(r) if r else None

    def history(self, serial):
        serial = serial.strip()
        events = sc.get_history(self.conn, serial)
        blocks = [dict(block=b.index, timestamp=b.timestamp, hash=b.hash, previous_hash=b.previous_hash)
                  for b in self.ledger.blocks_for_serial(serial)]
        return events, blocks

    def verify(self, serial):
        eng = vf.VerificationEngine(self.conn, self.ledger, reference_date=self.reference_date)
        return eng.verify_serial(serial)

    def stats(self):
        q = lambda t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return dict(products=q("products"), events=q("events"), blocks=len(self.ledger),
                    rejected=q("rejected_attempts"))

    def stage_counts(self):
        """Number of products currently at each stage of the supply chain (for the dashboard)."""
        got = {r[0]: r[1] for r in self.conn.execute("SELECT current_status, COUNT(*) FROM products GROUP BY current_status")}
        return [(role, got.get(status, 0)) for role, status in
                (("Manufacturer", sc.STATUS_BY_ROLE["Manufacturer"]), ("Distributor", sc.STATUS_BY_ROLE["Distributor"]),
                 ("Wholesaler", sc.STATUS_BY_ROLE["Wholesaler"]), ("Pharmacy", sc.STATUS_BY_ROLE["Pharmacy"]))]

    def recent_events(self, limit=8):
        return [dict(r) for r in self.conn.execute("SELECT * FROM events ORDER BY event_id DESC LIMIT ?", (limit,))]

    # ---------------------------------------------------------------------- ledger
    def ledger_status(self, use_anchor=False):
        anchor = self.load_anchor() if use_anchor else None
        eng = vf.VerificationEngine(self.conn, self.ledger, reference_date=self.reference_date,
                                    expected_anchor=(anchor["head_hash"], anchor["length"]) if anchor else None)
        res = eng.check_ledger()
        res.update(head_hash=self.ledger.head.hash, anchor_used=bool(anchor))
        if use_anchor and not anchor:
            res["message"] = "No saved anchor found - save one first"
        return res

    def save_anchor(self):
        head, length = self.ledger.anchor()
        data = {"head_hash": head, "length": length,
                "saved_at": datetime.now().strftime(sc.FMT_TS)}
        with open(self.anchor_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data

    def load_anchor(self):
        if not os.path.exists(self.anchor_path):
            return None
        with open(self.anchor_path, encoding="utf-8") as f:
            return json.load(f)

    def tamper_demo(self, block_index):
        """Alter a block in a COPY of the chain and show that verification catches it.
        The real ledger is never modified."""
        if not 1 <= block_index < len(self.ledger.blocks):
            raise ValueError(f"Block index must be between 1 and {len(self.ledger.blocks) - 1}")
        blocks = copy.deepcopy(self.ledger.blocks)
        before = lg.verify_blocks(blocks)
        blocks[block_index].data["receiver"] = "Tampered_Receiver"
        after = lg.verify_blocks(blocks)
        return dict(block_index=block_index, before_ok=before.ok, after_ok=after.ok,
                    issues=[i._asdict() for i in after.issues][:10],
                    message=(f"Copy of block {block_index} altered: verification "
                             f"{'MISSED the change' if after.ok else 'detected the change'}"))

    # ---------------------------------------------------------------------- alerts
    def alerts(self, source="app"):
        """Flagged records, highest priority first. source: 'app' or 'dataset' (raw Phase 1 database)."""
        if source == "dataset":
            if not os.path.exists(self.raw_db_path):
                raise FileNotFoundError(f"{self.raw_db_path} not found - run generate_dataset.py first")
            src = sqlite3.connect(self.raw_db_path)
            conn = sqlite3.connect(":memory:")
            src.backup(conn)
            src.close()
            conn.row_factory = sqlite3.Row
            eng = vf.VerificationEngine(conn, None, reference_date=self.reference_date)
        else:
            conn = self.conn
            eng = vf.VerificationEngine(conn, self.ledger, reference_date=self.reference_date)
        reps = [r for r in eng.verify_all() if r["flagged"]]
        if source == "dataset":
            conn.close()
        reps.sort(key=lambda r: (vf.PRIORITY.index(r["status"]), r["serial_number"]))
        return reps

    def rejected_attempts(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM rejected_attempts ORDER BY attempt_id DESC")]

    # --------------------------------------------------------------------- results
    def results_files(self):
        files = set()
        for pat in ("*summary*.csv", "*confusion*.csv", "*results*.csv"):
            files.update(glob.glob(os.path.join(self.logs_dir, pat)))
        return sorted(files)

    @staticmethod
    def read_table(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        return (rows[0], rows[1:]) if rows else ([], [])

    # ---------------------------------------------------------------- dataset loader
    def load_synthetic_dataset(self, progress=None):
        """Erase application data and replay the synthetic dataset through the normal
        register/transfer path (accepted events -> database + ledger; rejected -> attempt log)."""
        def read(name):
            with open(os.path.join(self.data_dir, name), newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        products = {p["record_id"]: p for p in read("products.csv")}
        events = read("events.csv")
        self.reset()
        accepted = rejected = 0
        if progress:
            progress(0, len(events))
        for i, e in enumerate(events, 1):
            if e["event_type"] == "REGISTER":
                res = self.register(products[e["record_id"]], e["sender"], e["timestamp"],
                                    e["authorization_evidence"])
            else:
                res = self.transfer(e["serial_number"], e["sender"], e["receiver"],
                                    e["authorization_evidence"], e["timestamp"])
            accepted += res.ok
            rejected += (not res.ok)
            if progress and (i % 20 == 0 or i == len(events)):
                progress(i, len(events))
        return dict(accepted=accepted, rejected=rejected, blocks=len(self.ledger))
