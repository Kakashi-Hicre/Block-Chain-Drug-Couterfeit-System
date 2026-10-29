"""
database.py - SQLite schema for the pharmaceutical traceability prototype.

All data used with this project is SYNTHETIC. No real medicine records are used.
"""
import sqlite3

DB_PATH = "pharma_traceability.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    participant_id TEXT PRIMARY KEY,          -- e.g. Distributor_A
    role           TEXT NOT NULL CHECK (role IN
                   ('Manufacturer','Distributor','Wholesaler','Pharmacy','Unknown')),
    authorized     INTEGER NOT NULL CHECK (authorized IN (0,1))
);

-- Master record of each batch: used later to detect data mismatches.
CREATE TABLE IF NOT EXISTS batches (
    batch_number     TEXT PRIMARY KEY,
    drug_id          TEXT NOT NULL,
    product_name     TEXT NOT NULL,
    manufacturer     TEXT NOT NULL REFERENCES participants(participant_id),
    manufacturing_date TEXT NOT NULL,         -- ISO date YYYY-MM-DD
    expiry_date      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    serial_number    TEXT PRIMARY KEY,
    drug_id          TEXT NOT NULL,
    product_name     TEXT NOT NULL,
    batch_number     TEXT NOT NULL,
    manufacturer     TEXT NOT NULL,
    manufacturing_date TEXT NOT NULL,
    expiry_date      TEXT NOT NULL,
    current_owner    TEXT NOT NULL,
    current_status   TEXT NOT NULL,
    is_synthetic     INTEGER NOT NULL DEFAULT 1
);

-- Custody history. In Phase 3 each accepted event will also become a ledger block.
CREATE TABLE IF NOT EXISTS events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_number    TEXT NOT NULL,
    event_type       TEXT NOT NULL CHECK (event_type IN ('REGISTER','TRANSFER')),
    sender           TEXT NOT NULL,
    receiver         TEXT NOT NULL,
    timestamp        TEXT NOT NULL,           -- ISO datetime
    authorization_evidence TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_serial ON events(serial_number);
"""


def get_connection(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def reset_database(path: str = DB_PATH) -> sqlite3.Connection:
    """Delete all tables and recreate them empty."""
    conn = get_connection(path)
    for t in ("ledger_blocks", "events", "products", "batches", "participants"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    create_schema(conn)
    return conn


if __name__ == "__main__":
    c = reset_database()
    print("Schema created in", DB_PATH)
    c.close()
