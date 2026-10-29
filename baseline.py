"""
baseline.py - Phase 6: the conventional SQLite-only traceability system used as the comparison baseline.

All data is SYNTHETIC.

The baseline is deliberately FAIR to the conventional approach. It uses:
  * the same tables, the same registration/transfer business rules (access control, ownership,
    ordered supply-chain stages, duplicate-serial rejection) - supply_chain.py with ledger=None;
  * the same rule-based verification engine (duplicates, unauthorized transfers, missing custody
    events, data mismatch, expiry) - verifier.py with no ledger attached.
The ONLY thing it lacks is the SHA-256 hash-linked ledger. Any difference measured in Phase 6
is therefore the effect of the ledger (extra tamper-evidence/auditability vs. computational and
storage overhead), not of a weaker rule set.
"""
from datetime import date

import supply_chain as sc
import verifier as vf

REFERENCE_DATE = date(2026, 6, 1)


class SQLiteBaseline:
    name = "baseline"

    def __init__(self, conn, reference_date=REFERENCE_DATE):
        self.conn = conn
        self.ledger = None
        self.reference_date = reference_date

    def register(self, product, registrant, timestamp=None, evidence=None):
        return sc.register_product(self.conn, product, registrant, timestamp, evidence,
                                   ledger=self.ledger)

    def transfer(self, serial, sender, receiver, evidence, timestamp=None):
        return sc.transfer_product(self.conn, serial, sender, receiver, evidence, timestamp,
                                   ledger=self.ledger)

    def history(self, serial):
        return sc.get_history(self.conn, serial)

    def engine(self, **kw):
        return vf.VerificationEngine(self.conn, self.ledger, reference_date=self.reference_date, **kw)

    def verify(self, serial):
        return self.engine().verify_serial(serial)
