"""
ledger.py - Phase 3: hash-linked ledger (blockchain PROTOTYPE) using SHA-256.

All data is SYNTHETIC.

IMPORTANT - what this is and is not:
  * It is a single-node hash chain. It demonstrates TAMPER EVIDENCE: if a stored block
    is altered, removed, inserted or reordered, verify_chain() reports an integrity failure.
  * It is NOT a distributed blockchain: no independent nodes, no consensus, no
    permissioned identities. Describe it as a "blockchain prototype / simulation".
  * A hash chain alone cannot detect (a) truncation of the newest blocks, or (b) a
    complete rewrite in which an attacker recomputes every later hash. Both are caught
    only by comparing against an EXTERNALLY STORED anchor (head hash + chain length).
    verify_chain(expected_head=..., expected_length=...) supports this check.
  * Verifying the ledger proves the RECORD was not changed afterwards. It does not prove
    a medicine is physically authentic.

Use with supply_chain.py:
    ledger = Ledger(conn)
    register_product(conn, product, "Manufacturer_A", ledger=ledger)
    transfer_product(conn, serial, sender, receiver, evidence, ledger=ledger)
"""
import copy
import hashlib
import json
from collections import namedtuple
from dataclasses import dataclass, field
from datetime import datetime

GENESIS_PREV_HASH = "0" * 64
GENESIS_TIMESTAMP = "2026-01-01 00:00:00"      # fixed so the genesis hash is reproducible
GENESIS_DATA = {"type": "GENESIS", "note": "Synthetic pharmaceutical traceability ledger"}
FMT_TS = "%Y-%m-%d %H:%M:%S"

# Integrity-failure codes
GENESIS_INVALID = "GENESIS_INVALID"
INDEX_MISMATCH = "INDEX_MISMATCH"
HASH_MISMATCH = "HASH_MISMATCH"       # block content does not match its stored hash
LINK_BROKEN = "LINK_BROKEN"           # previous_hash does not match the preceding block
HEAD_MISMATCH = "HEAD_MISMATCH"       # newest hash differs from the external anchor
LENGTH_MISMATCH = "LENGTH_MISMATCH"   # chain length differs from the external anchor
EMPTY_CHAIN = "EMPTY_CHAIN"

Issue = namedtuple("Issue", "block_index code message")
VerifyResult = namedtuple("VerifyResult", "ok issues")


@dataclass
class Block:
    index: int
    timestamp: str
    data: dict
    previous_hash: str
    hash: str = field(default="")

    def compute_hash(self) -> str:
        """SHA-256 over a deterministic (sorted-key, fixed-separator) JSON representation."""
        payload = json.dumps(
            {"index": self.index, "timestamp": self.timestamp, "data": self.data,
             "previous_hash": self.previous_hash},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def seal(self):
        self.hash = self.compute_hash()
        return self


def make_genesis() -> Block:
    return Block(0, GENESIS_TIMESTAMP, dict(GENESIS_DATA), GENESIS_PREV_HASH).seal()


def verify_blocks(blocks, expected_head=None, expected_length=None) -> VerifyResult:
    """
    Recalculate every hash and check every link. Returns VerifyResult(ok, issues).
    expected_head / expected_length: optional external anchor (see module docstring).
    """
    issues = []
    if not blocks:
        return VerifyResult(False, [Issue(-1, EMPTY_CHAIN, "Chain contains no blocks")])

    g = blocks[0]
    if g.index != 0 or g.previous_hash != GENESIS_PREV_HASH:
        issues.append(Issue(0, GENESIS_INVALID, "First block is not a valid genesis block"))

    for pos, b in enumerate(blocks):
        if b.index != pos:
            issues.append(Issue(pos, INDEX_MISMATCH,
                                f"Block at position {pos} claims index {b.index}"))
        if b.compute_hash() != b.hash:
            issues.append(Issue(pos, HASH_MISMATCH,
                                f"Block {b.index}: content does not match its stored hash"))
        if pos > 0 and b.previous_hash != blocks[pos - 1].hash:
            issues.append(Issue(pos, LINK_BROKEN,
                                f"Block {b.index}: previous_hash does not match block {pos - 1}"))

    if expected_head is not None and blocks[-1].hash != expected_head:
        issues.append(Issue(len(blocks) - 1, HEAD_MISMATCH,
                            "Newest block hash differs from the externally stored anchor"))
    if expected_length is not None and len(blocks) != expected_length:
        issues.append(Issue(len(blocks) - 1, LENGTH_MISMATCH,
                            f"Chain has {len(blocks)} blocks, anchor says {expected_length}"))
    return VerifyResult(not issues, issues)


class Ledger:
    """
    Hash-linked ledger. In-memory when conn is None; otherwise persisted in the SQLite
    table `ledger_blocks` (created if missing) so it survives restarts and can be re-verified.
    """

    def __init__(self, conn=None):
        self.conn = conn
        self.blocks = []
        if conn is not None:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ledger_blocks ("
                "block_index INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, data TEXT NOT NULL, "
                "previous_hash TEXT NOT NULL, hash TEXT NOT NULL)")
            conn.commit()
            self._load()
        if not self.blocks:
            genesis = make_genesis()
            self.blocks.append(genesis)
            self._store(genesis)

    # ---- persistence
    def _store(self, b: Block):
        if self.conn is None:
            return
        self.conn.execute(
            "INSERT INTO ledger_blocks (block_index, timestamp, data, previous_hash, hash) "
            "VALUES (?,?,?,?,?)",
            (b.index, b.timestamp, json.dumps(b.data, sort_keys=True), b.previous_hash, b.hash))
        self.conn.commit()

    def _load(self):
        rows = self.conn.execute(
            "SELECT block_index, timestamp, data, previous_hash, hash FROM ledger_blocks "
            "ORDER BY block_index").fetchall()
        self.blocks = [Block(r[0], r[1], json.loads(r[2]), r[3], r[4]) for r in rows]

    def reload(self):
        """Re-read the chain from the database (e.g. to detect tampering done directly in SQLite)."""
        if self.conn is not None:
            self._load()

    # ---- writing
    @property
    def head(self) -> Block:
        return self.blocks[-1]

    def add_block(self, data: dict, timestamp: str = None) -> Block:
        b = Block(index=len(self.blocks),
                  timestamp=timestamp or datetime.now().strftime(FMT_TS),
                  data=copy.deepcopy(data),
                  previous_hash=self.head.hash).seal()
        self.blocks.append(b)
        self._store(b)
        return b

    def add_event(self, event: dict) -> Block:
        """Hook called by supply_chain for every ACCEPTED event. Block time = event time."""
        return self.add_block(event, timestamp=event.get("timestamp"))

    # ---- reading / verifying
    def anchor(self):
        """(head_hash, length): store this OUTSIDE the database to catch truncation/rewrite."""
        return self.head.hash, len(self.blocks)

    def verify_chain(self, expected_head=None, expected_length=None) -> VerifyResult:
        return verify_blocks(self.blocks, expected_head, expected_length)

    def blocks_for_serial(self, serial_number: str):
        return [b for b in self.blocks if b.data.get("serial_number") == serial_number]

    def __len__(self):
        return len(self.blocks)
