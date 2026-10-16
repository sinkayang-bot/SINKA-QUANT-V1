"""Independent Firestore lease for OKX read-only sync workers.

This lease does not apply to order submission and cannot unlock live trading.
"""
import time
import uuid
from google.cloud import firestore
from okx_firestore_sync import OKXSyncLedger

LEASE_SECONDS = 30

class OKXSyncLease:
    def __init__(self, ledger: OKXSyncLedger, worker_id=None):
        self.ledger = ledger
        self.worker_id = worker_id or uuid.uuid4().hex

    @staticmethod
    @firestore.transactional
    def _acquire(tx, self, now):
        ref = self.ledger.lease_ref
        snap = ref.get(transaction=tx)
        previous = snap.to_dict() if snap.exists else {}
        if (previous.get("worker_id") not in (None, self.worker_id)
                and float(previous.get("expires_at", 0)) > now):
            return False
        tx.set(ref, {"worker_id": self.worker_id,
                     "expires_at": now + LEASE_SECONDS,
                     "updated_at": firestore.SERVER_TIMESTAMP})
        return True

    def acquire(self):
        return self._acquire(self.ledger.db.transaction(), self, time.time())

    @staticmethod
    @firestore.transactional
    def _release(tx, self):
        snap = self.ledger.lease_ref.get(transaction=tx)
        if snap.exists and snap.to_dict().get("worker_id") == self.worker_id:
            tx.delete(self.ledger.lease_ref)
            return True
        return False

    def release(self):
        return self._release(self.ledger.db.transaction(), self)
