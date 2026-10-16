"""Lease-fenced snapshots of OKX USDT swap positions for diagnostics.

A stored snapshot never grants trading authorization or resolves filled orders.
"""
import time
from google.cloud import firestore
from okx_reconcile import number
from okx_readonly import ALLOWED_SYMBOLS

def normalize_positions(rows):
    if not isinstance(rows, list):
        raise ValueError("Expected OKX positions list")
    if len(rows) > 1000:
        raise ValueError("Position list exceeds safety bound")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid OKX position")
        quantity = number(row.get("pos"), allow_negative=True)
        if quantity == 0:
            continue
        if row.get("instId") not in ALLOWED_SYMBOLS:
            raise ValueError("Unrecognized position instrument")
        if row.get("mgnMode") != "isolated":
            raise ValueError("Non-isolated position")
        side = row.get("posSide")
        if side not in ("net", "long", "short"):
            raise ValueError("Invalid position side")
        if side in ("long", "short") and quantity <= 0:
            raise ValueError("Hedge-mode contracts must be positive")
        result.append({"symbol": row["instId"], "posSide": side,
                       "contracts": str(quantity), "marginMode": "isolated"})
    if len(result) > 1:
        raise ValueError("Multiple non-zero positions require investigation")
    return result

class OKXPositionJournal:
    def __init__(self, sync_ledger):
        self.db = sync_ledger.db
        self.lease_ref = sync_ledger.lease_ref
        self.ref = sync_ledger.lease_ref.parent.document("position_snapshot")

    @firestore.transactional
    def _write(self, tx, worker_id, positions, now):
        lease_snap = self.lease_ref.get(transaction=tx)
        lease = lease_snap.to_dict() if lease_snap.exists else {}
        if (not worker_id or lease.get("worker_id") != worker_id
                or float(lease.get("expires_at", 0)) <= now):
            return False
        snap = self.ref.get(transaction=tx)
        previous = snap.to_dict() if snap.exists else {}
        tx.set(self.ref, {
            "positions": positions,
            "sequence": int(previous.get("sequence", 0)) + 1,
            "source": "okx_readonly",
            "writer": worker_id,
            "updated_at": firestore.SERVER_TIMESTAMP,
            "trading_authorized": False,
        })
        return True

    def write_snapshot(self, worker_id, exchange_positions):
        snapshot = normalize_positions(exchange_positions)
        return self._write(self.db.transaction(), worker_id, snapshot, time.time())

    def read_diagnostic(self):
        snap = self.ref.get()
        return snap.to_dict() if snap.exists else None
