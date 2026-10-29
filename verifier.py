"""
verifier.py - Phase 4: rule-based drug verification engine.

All data is SYNTHETIC.

Given a serial number the engine checks the registered product, the custody history and
(optionally) the ledger, and returns a report naming every failed rule.

IMPORTANT WORDING (from the project brief):
  * The engine reports "record verified" - NOT that a medicine is physically authentic.
  * A suspicious record is an indication that needs investigation, not proof of counterfeiting.
  * An expired product is a separate product-status condition, not a counterfeit indicator.

Rules (each failure becomes one or more findings):
  UNREGISTERED_PRODUCT_ALERT   serial number is not in the product register
  DUPLICATE_SERIAL_ALERT       serial number was registered more than once
  UNAUTHORIZED_TRANSFER_ALERT  an event involves an unauthorized participant, a registration by a
                               non-manufacturer, or a transfer without authorization evidence
  DATA_MISMATCH_ALERT          product record disagrees with the batch master record / registrant
  TRACEABILITY_WARNING         custody chain is broken: missing registration or hand-over, wrong
                               holder, skipped stage, or history disagrees with the product record
  EXPIRY_WARNING               expiry date is before the reference date
  BLOCKCHAIN_INTEGRITY_ALERT   (only when a ledger is attached) a ledger block for this serial fails
                               verification, or the database history differs from the ledger record

The overall status is the highest-priority finding (see PRIORITY); all findings are still listed.
"""
from datetime import date, datetime

import ledger as lg
import supply_chain as sc

# "Today" for expiry checks. Fixed so the synthetic experiments are reproducible; pass
# reference_date=date.today() for live use.
REFERENCE_DATE = date(2026, 6, 1)

RECORD_VERIFIED = "RECORD_VERIFIED"
UNREGISTERED = "UNREGISTERED_PRODUCT_ALERT"
DUPLICATE = "DUPLICATE_SERIAL_ALERT"
UNAUTHORIZED = "UNAUTHORIZED_TRANSFER_ALERT"
MISMATCH = "DATA_MISMATCH_ALERT"
TRACEABILITY = "TRACEABILITY_WARNING"
EXPIRY = "EXPIRY_WARNING"
BLOCKCHAIN = "BLOCKCHAIN_INTEGRITY_ALERT"

PRIORITY = [BLOCKCHAIN, DUPLICATE, UNREGISTERED, UNAUTHORIZED, MISMATCH, TRACEABILITY, EXPIRY]
SEVERITY = {BLOCKCHAIN: "alert", DUPLICATE: "alert", UNREGISTERED: "alert", UNAUTHORIZED: "alert",
            MISMATCH: "alert", TRACEABILITY: "warning", EXPIRY: "warning"}

MSG_VERIFIED = ("Record verified: the registered data and custody history are consistent. "
                "This does not prove that the medicine is physically authentic.")
MSG_EXPIRED = ("Expired product: this is a product-status condition and does not by itself "
               "indicate counterfeiting.")
MSG_SUSPICIOUS = ("Suspicious record: this is an indication that further investigation is needed, "
                  "not proof of counterfeiting.")

LEDGER_FIELDS = ("event_type", "sender", "receiver", "timestamp", "authorization_evidence")
BATCH_FIELDS = ("drug_id", "product_name", "manufacturer", "manufacturing_date", "expiry_date")


def _finding(rule, message, event_id=None):
    return {"rule": rule, "severity": SEVERITY[rule], "message": message, "event_id": event_id}


def _event_key(e):
    return tuple(e.get(f) for f in LEDGER_FIELDS)


class VerificationEngine:
    def __init__(self, conn, ledger=None, reference_date=REFERENCE_DATE, expected_anchor=None):
        """
        conn            SQLite connection (database.py schema)
        ledger          optional Ledger; enables the BLOCKCHAIN_INTEGRITY_ALERT rule. All events in
                        the database must then also be in the ledger (accepted events only).
        expected_anchor optional (head_hash, length) stored OUTSIDE the database
        The ledger result is cached per engine instance: create a new engine after changes.
        """
        self.conn = conn
        self.ledger = ledger
        self.reference_date = reference_date
        self.expected_anchor = expected_anchor
        self._lcache = None

    # ------------------------------------------------------------------ public API
    def verify_serial(self, serial_number):
        serial = str(serial_number).strip()
        row = sc.get_product(self.conn, serial)
        product = dict(row) if row is not None else None
        history = sc.get_history(self.conn, serial)
        findings = []

        if product is None:
            findings.append(_finding(
                UNREGISTERED, f"Serial number {serial} is not in the product register"
                + (f" ({len(history)} event(s) refer to it)" if history else "")))
            return self._report(serial, None, history, findings)

        roles = self._participants()
        self._check_duplicates(history, findings)
        skip_ids = self._check_authorization(history, roles, findings)
        self._check_traceability(product, history, roles, skip_ids, findings)
        self._check_data(product, history, findings)
        self._check_expiry(product, findings)
        if self.ledger is not None:
            self._check_ledger(serial, history, findings)
        return self._report(serial, product, history, findings)

    def verify_all(self, serials=None):
        if serials is None:
            serials = [r[0] for r in self.conn.execute(
                "SELECT serial_number FROM products UNION SELECT serial_number FROM events "
                "ORDER BY 1")]
        return [self.verify_serial(s) for s in serials]

    def check_ledger(self):
        """System-level ledger check (for the 'Check blockchain integrity' menu option)."""
        if self.ledger is None:
            return {"ok": None, "blocks": 0, "issues": [], "message": "No ledger attached"}
        res, _, _, _ = self._ledger_info()
        return {"ok": res.ok, "blocks": len(self.ledger),
                "issues": [i._asdict() for i in res.issues],
                "message": "Ledger integrity verified" if res.ok
                else f"Ledger integrity FAILED ({len(res.issues)} issue(s))"}

    @staticmethod
    def format_report(rep):
        lines = ["=" * 72, f"Verification report - serial {rep['serial_number']}", "=" * 72,
                 f"Result : {rep['status']}", f"Note   : {rep['message']}"]
        p = rep["product"]
        if p:
            lines.append(f"Product: {p['product_name']} ({p['drug_id']}), batch {p['batch_number']}, "
                         f"manufacturer {p['manufacturer']}")
            lines.append(f"         made {p['manufacturing_date']}, expires {p['expiry_date']}, "
                         f"owner {p['current_owner']} [{p['current_status']}]")
        if rep["findings"]:
            lines.append("Failed rules:")
            for f in rep["findings"]:
                lines.append(f"  - [{f['severity'].upper()}] {f['rule']}: {f['message']}")
        lines.append("Event history:")
        if rep["history"]:
            for e in rep["history"]:
                lines.append(f"  #{e['event_id']:<5} {e['timestamp']}  {e['event_type']:<8} "
                             f"{e['sender']} -> {e['receiver']}")
        else:
            lines.append("  (no events)")
        return "\n".join(lines)

    # ------------------------------------------------------------------ rules
    def _participants(self):
        return {r["participant_id"]: (r["role"], bool(r["authorized"]))
                for r in self.conn.execute("SELECT * FROM participants")}

    def _check_duplicates(self, history, findings):
        regs = [e for e in history if e["event_type"] == "REGISTER"]
        if len(regs) > 1:
            detail = "; ".join(f"{e['sender']} at {e['timestamp']}" for e in regs)
            findings.append(_finding(
                DUPLICATE, f"Serial number was registered {len(regs)} times ({detail})"))

    def _check_authorization(self, history, roles, findings):
        """Returns the ids of events with authorization problems (chain checks skip them)."""
        skip = set()
        for e in history:
            reasons = []
            s, r = roles.get(e["sender"]), roles.get(e["receiver"])
            if s is None or not s[1]:
                reasons.append(f"sender {e['sender']} is not an authorized participant")
            if r is None or not r[1]:
                reasons.append(f"receiver {e['receiver']} is not an authorized participant")
            if e["event_type"] == "REGISTER":
                if s is not None and s[1] and s[0] != "Manufacturer":
                    reasons.append("registration by a non-manufacturer")
            else:
                ev = str(e.get("authorization_evidence") or "").strip()
                if not ev or ev.upper() == "NONE":
                    reasons.append("no authorization evidence recorded")
            if reasons:
                skip.add(e["event_id"])
                findings.append(_finding(
                    UNAUTHORIZED,
                    f"Event #{e['event_id']} ({e['event_type']} {e['sender']} -> {e['receiver']} "
                    f"at {e['timestamp']}): " + "; ".join(reasons), e["event_id"]))
        return skip

    def _check_traceability(self, product, history, roles, skip_ids, findings):
        """
        Walk the custody chain. Events flagged by the authorization rule are not also reported
        here (the owner simply advances to their receiver) so one fault is reported once.
        Extra REGISTER events are covered by the duplicate rule.
        """
        if not history:
            findings.append(_finding(TRACEABILITY, "No custody events are recorded for this product"))
            return
        if history[0]["event_type"] != "REGISTER":
            findings.append(_finding(TRACEABILITY, "History does not begin with a registration event"))

        owner, seen_register = None, False
        for e in history:
            if e["event_type"] == "REGISTER":
                if not seen_register:
                    owner, seen_register = e["receiver"], True
                continue
            if e["event_id"] in skip_ids:
                owner = e["receiver"]
                continue
            if owner is not None and e["sender"] != owner:
                findings.append(_finding(
                    TRACEABILITY,
                    f"Event #{e['event_id']}: {e['sender']} handed over the product but the last "
                    f"recorded holder was {owner} (custody event missing)", e["event_id"]))
            s, r = roles.get(e["sender"]), roles.get(e["receiver"])
            if s and r and s[0] in sc.ROLE_ORDER and r[0] in sc.ROLE_ORDER and \
                    sc.ROLE_ORDER.index(r[0]) != sc.ROLE_ORDER.index(s[0]) + 1:
                findings.append(_finding(
                    TRACEABILITY,
                    f"Event #{e['event_id']}: {s[0]} -> {r[0]} skips or reverses a supply-chain "
                    f"stage", e["event_id"]))
            owner = e["receiver"]

        if owner != product["current_owner"]:
            findings.append(_finding(
                TRACEABILITY, f"History ends with holder {owner} but the product record says "
                              f"{product['current_owner']}"))
        owner_role = roles.get(product["current_owner"], (None, False))[0]
        if owner_role in sc.STATUS_BY_ROLE and product["current_status"] != sc.STATUS_BY_ROLE[owner_role]:
            findings.append(_finding(
                TRACEABILITY, f"Status '{product['current_status']}' is inconsistent with holder "
                              f"{product['current_owner']} ({owner_role})"))

    def _check_data(self, product, history, findings):
        batch = self.conn.execute("SELECT * FROM batches WHERE batch_number = ?",
                                  (product["batch_number"],)).fetchone()
        if batch is None:
            findings.append(_finding(MISMATCH, f"Batch {product['batch_number']} is not in the batch "
                                               f"master record"))
        else:
            diffs = [f"{f}: product says '{product[f]}', batch master says '{batch[f]}'"
                     for f in BATCH_FIELDS if str(product[f]) != str(batch[f])]
            if diffs:
                findings.append(_finding(MISMATCH, "Product record differs from batch master - "
                                         + "; ".join(diffs)))
        regs = [e for e in history if e["event_type"] == "REGISTER"]
        if regs and regs[0]["sender"] != product["manufacturer"]:
            findings.append(_finding(
                MISMATCH, f"Product lists manufacturer {product['manufacturer']} but it was registered "
                          f"by {regs[0]['sender']}"))

    def _check_expiry(self, product, findings):
        try:
            exp = datetime.strptime(product["expiry_date"], sc.FMT_DATE).date()
        except (ValueError, TypeError):
            findings.append(_finding(MISMATCH, f"Expiry date '{product['expiry_date']}' is not a valid date"))
            return
        if exp < self.reference_date:
            findings.append(_finding(EXPIRY, f"Product expired on {product['expiry_date']} "
                                             f"(reference date {self.reference_date.isoformat()})"))

    # ---------------------------------------------------------------- ledger rule
    def _ledger_info(self):
        if self._lcache is None:
            head, length = self.expected_anchor if self.expected_anchor else (None, None)
            res = self.ledger.verify_chain(expected_head=head, expected_length=length)
            implicated, index_issues, global_fail = set(), [], False
            for i in res.issues:
                if i.code in (lg.HASH_MISMATCH, lg.GENESIS_INVALID):
                    implicated.add(i.block_index)
                elif i.code == lg.LINK_BROKEN:                 # either neighbour may be the altered one
                    implicated.update({i.block_index, i.block_index - 1})
                elif i.code == lg.INDEX_MISMATCH:
                    index_issues.append(i.block_index)
                else:                                          # empty chain / external anchor failed
                    global_fail = True
            if index_issues:                                   # deletion shifts every later index:
                p = min(index_issues)                          # only the first gap is informative
                implicated.update({p, p - 1})
            positions = {}
            for pos, b in enumerate(self.ledger.blocks):
                s = b.data.get("serial_number")
                if s:
                    positions.setdefault(s, []).append(pos)
            self._lcache = (res, implicated, global_fail, positions)
        return self._lcache

    def _check_ledger(self, serial, history, findings):
        _, implicated, global_fail, positions = self._ledger_info()
        pos_list = positions.get(serial, [])
        if global_fail:
            findings.append(_finding(BLOCKCHAIN, "Ledger failed the chain-level / external-anchor "
                                                 "check, so its records cannot be trusted"))
        bad = [p for p in pos_list if p in implicated]
        if bad:
            findings.append(_finding(BLOCKCHAIN, f"Ledger block(s) {bad} for this serial fail "
                                                 f"hash/link verification"))
        db_seq = [_event_key(e) for e in history]
        led_seq = [_event_key(self.ledger.blocks[p].data) for p in pos_list]
        if db_seq != led_seq:
            findings.append(_finding(BLOCKCHAIN, f"Database history ({len(db_seq)} events) differs "
                                                 f"from the ledger record ({len(led_seq)} blocks)"))

    # ---------------------------------------------------------------------- report
    def _report(self, serial, product, history, findings):
        rules = {f["rule"] for f in findings}
        status = next((r for r in PRIORITY if r in rules), RECORD_VERIFIED)
        suspicious = bool(rules - {EXPIRY})
        if not rules:
            message = MSG_VERIFIED
        elif not suspicious:
            message = MSG_EXPIRED
        else:
            message = MSG_SUSPICIOUS
        return {"serial_number": serial, "status": status, "flagged": bool(findings),
                "suspicious": suspicious, "findings": findings, "product": product,
                "history": history, "message": message}
