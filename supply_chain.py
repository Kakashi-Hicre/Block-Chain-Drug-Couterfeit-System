"""
supply_chain.py - Phase 2: product registration and supply-chain tracking.

All data is SYNTHETIC.

Every function returns a Result(ok, code, message). Rejected operations change
NOTHING in the database (no partial writes). Accepted events are appended to the
events table, never overwritten.

Phase 3 hook: pass a `ledger` object with an add_event(event: dict) method and every
ACCEPTED event is also handed to it. Rejected events are never sent to the ledger.
"""
from collections import namedtuple
from datetime import datetime

Result = namedtuple("Result", "ok code message")

FMT_TS = "%Y-%m-%d %H:%M:%S"
FMT_DATE = "%Y-%m-%d"

# Valid custody path: each hand-over must move exactly one step forward.
ROLE_ORDER = ["Manufacturer", "Distributor", "Wholesaler", "Pharmacy"]
STATUS_BY_ROLE = {
    "Manufacturer": "Registered",
    "Distributor": "In distribution",
    "Wholesaler": "At wholesaler",
    "Pharmacy": "At pharmacy",
}

REQUIRED_FIELDS = ["serial_number", "drug_id", "product_name", "batch_number",
                   "manufacturer", "manufacturing_date", "expiry_date"]

# Rejection codes
OK = "OK"
MISSING_FIELD = "MISSING_FIELD"
INVALID_DATE = "INVALID_DATE"
UNAUTHORIZED_REGISTRANT = "UNAUTHORIZED_REGISTRANT"
DUPLICATE_SERIAL = "DUPLICATE_SERIAL"
UNKNOWN_PRODUCT = "UNKNOWN_PRODUCT"
UNAUTHORIZED_SENDER = "UNAUTHORIZED_SENDER"
UNAUTHORIZED_RECEIVER = "UNAUTHORIZED_RECEIVER"
MISSING_EVIDENCE = "MISSING_EVIDENCE"
INVALID_OWNERSHIP = "INVALID_OWNERSHIP"
INVALID_TRANSITION = "INVALID_TRANSITION"
INVALID_TIMESTAMP = "INVALID_TIMESTAMP"


def _now():
    return datetime.now().strftime(FMT_TS)


def _parse(value, fmt):
    try:
        return datetime.strptime(str(value), fmt)
    except (ValueError, TypeError):
        return None


def get_participant(conn, participant_id):
    return conn.execute("SELECT * FROM participants WHERE participant_id = ?",
                        (participant_id,)).fetchone()


def get_product(conn, serial_number):
    return conn.execute("SELECT * FROM products WHERE serial_number = ?",
                        (serial_number,)).fetchone()


def get_history(conn, serial_number):
    """Full custody history of a serial number, oldest first."""
    rows = conn.execute(
        "SELECT * FROM events WHERE serial_number = ? ORDER BY timestamp, event_id",
        (serial_number,)).fetchall()
    return [dict(r) for r in rows]


def _insert_event(conn, serial, event_type, sender, receiver, timestamp, evidence):
    cur = conn.execute(
        "INSERT INTO events (serial_number, event_type, sender, receiver, timestamp, "
        "authorization_evidence) VALUES (?,?,?,?,?,?)",
        (serial, event_type, sender, receiver, timestamp, evidence))
    return dict(event_id=cur.lastrowid, serial_number=serial, event_type=event_type,
                sender=sender, receiver=receiver, timestamp=timestamp,
                authorization_evidence=evidence)


def register_product(conn, product, registrant, timestamp=None, evidence=None, ledger=None):
    """
    Register a new product. `product` is a dict with REQUIRED_FIELDS.
    Checks, in order: required fields, date validity, registrant authorization,
    duplicate serial number. On success creates the product record (owner = registrant,
    status 'Registered') and a REGISTER event.
    """
    # 1. required fields
    missing = [f for f in REQUIRED_FIELDS if not str(product.get(f, "")).strip()]
    if missing:
        return Result(False, MISSING_FIELD, f"Missing required field(s): {', '.join(missing)}")

    serial = str(product["serial_number"]).strip()

    # 2. dates
    mfg = _parse(product["manufacturing_date"], FMT_DATE)
    exp = _parse(product["expiry_date"], FMT_DATE)
    if mfg is None or exp is None:
        return Result(False, INVALID_DATE, "Dates must use the format YYYY-MM-DD")
    if exp <= mfg:
        return Result(False, INVALID_DATE, "Expiry date must be after the manufacturing date")

    # 3. registrant must be an authorized manufacturer
    p = get_participant(conn, registrant)
    if p is None or not p["authorized"] or p["role"] != "Manufacturer":
        return Result(False, UNAUTHORIZED_REGISTRANT,
                      f"{registrant} is not an authorized manufacturer")

    # 4. duplicate serial number
    if get_product(conn, serial) is not None:
        return Result(False, DUPLICATE_SERIAL, f"Serial number {serial} is already registered")

    ts = timestamp or _now()
    ev_evidence = evidence or f"AUTH-{registrant}-{serial}"
    try:
        conn.execute(
            "INSERT INTO products (serial_number, drug_id, product_name, batch_number, "
            "manufacturer, manufacturing_date, expiry_date, current_owner, current_status, "
            "is_synthetic) VALUES (?,?,?,?,?,?,?,?,?,1)",
            (serial, product["drug_id"], product["product_name"], product["batch_number"],
             product["manufacturer"], product["manufacturing_date"], product["expiry_date"],
             registrant, STATUS_BY_ROLE["Manufacturer"]))
        event = _insert_event(conn, serial, "REGISTER", registrant, registrant, ts, ev_evidence)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if ledger is not None:
        ledger.add_event(event)
    return Result(True, OK, f"Product {serial} registered")


def transfer_product(conn, serial_number, sender, receiver, evidence, timestamp=None, ledger=None):
    """
    Transfer custody of a product from `sender` to `receiver`.
    Checks, in order: product exists, sender authorized, receiver authorized,
    authorization evidence present, sender is the current owner, hand-over moves exactly
    one step forward (Manufacturer -> Distributor -> Wholesaler -> Pharmacy), and the
    timestamp is later than the previous event.
    """
    product = get_product(conn, serial_number)
    if product is None:
        return Result(False, UNKNOWN_PRODUCT, f"Serial number {serial_number} is not registered")

    s = get_participant(conn, sender)
    if s is None or not s["authorized"]:
        return Result(False, UNAUTHORIZED_SENDER, f"{sender} is not an authorized participant")

    r = get_participant(conn, receiver)
    if r is None or not r["authorized"]:
        return Result(False, UNAUTHORIZED_RECEIVER, f"{receiver} is not an authorized participant")

    if not evidence or not str(evidence).strip() or str(evidence).strip().upper() == "NONE":
        return Result(False, MISSING_EVIDENCE, "Authorization evidence is required for a transfer")

    if product["current_owner"] != sender:
        return Result(False, INVALID_OWNERSHIP,
                      f"{sender} does not hold {serial_number} (current owner: "
                      f"{product['current_owner']})")

    if s["role"] not in ROLE_ORDER or r["role"] not in ROLE_ORDER or \
            ROLE_ORDER.index(r["role"]) != ROLE_ORDER.index(s["role"]) + 1:
        return Result(False, INVALID_TRANSITION,
                      f"{s['role']} -> {r['role']} is not a valid next step in the supply chain")

    ts = timestamp or _now()
    new_ts = _parse(ts, FMT_TS)
    if new_ts is None:
        return Result(False, INVALID_TIMESTAMP, "Timestamp must use the format YYYY-MM-DD HH:MM:SS")
    last = conn.execute("SELECT MAX(timestamp) FROM events WHERE serial_number = ?",
                        (serial_number,)).fetchone()[0]
    if last is not None and new_ts < _parse(last, FMT_TS):
        # equal timestamps are allowed (second resolution; order is kept by event_id)
        return Result(False, INVALID_TIMESTAMP, "Event must not be earlier than the previous event")

    try:
        event = _insert_event(conn, serial_number, "TRANSFER", sender, receiver, ts, evidence)
        conn.execute("UPDATE products SET current_owner = ?, current_status = ? "
                     "WHERE serial_number = ?",
                     (receiver, STATUS_BY_ROLE[r["role"]], serial_number))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if ledger is not None:
        ledger.add_event(event)
    return Result(True, OK, f"{serial_number}: {sender} -> {receiver}")
