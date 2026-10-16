"""Firestore lease for coordinating paper-only monitor instances.

A lease reduces duplicate polling during rolling restarts. The ledger's
transactional position-id guard remains the final duplicate-close protection.
"""
import time
import uuid
from google.cloud import firestore

LEASE_SECONDS = 30

class MonitorLease:
    def __init__(self, ledger, worker_id=None):
        self.ref = ledger.account.collection("runtime").document("lease")
        self.db = ledger.db
        self.worker_id = worker_id or uuid.uuid4().hex

    @staticmethod
    @firestore.transactional
    def _acquire(transaction, self, now):
        snapshot = self.ref.get(transaction=transaction)
        state = snapshot.to_dict() if snapshot.exists else {}
        current = state.get("worker_id")
        expires = float(state.get("expires_at", 0))
        if current and current != self.worker_id and expires > now:
            return False
        transaction.set(self.ref, {
            "worker_id": self.worker_id,
            "expires_at": now + LEASE_SECONDS,
            "renewed_at": firestore.SERVER_TIMESTAMP,
        })
        return True

    def acquire(self):
        return self._acquire(self.db.transaction(), self, time.time())

    @staticmethod
    @firestore.transactional
    def _release(transaction, self):
        snapshot = self.ref.get(transaction=transaction)
        if snapshot.exists and snapshot.to_dict().get("worker_id") == self.worker_id:
            transaction.delete(self.ref)

    def release(self):
        self._release(self.db.transaction(), self)

    def is_owner(self):
        snapshot = self.ref.get()
        if not snapshot.exists:
            return False
        data = snapshot.to_dict()
        return data.get("worker_id") == self.worker_id and float(data.get("expires_at", 0)) > time.time()
