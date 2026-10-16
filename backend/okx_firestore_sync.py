"""Firestore diagnostic store for OKX read-only sync; NEVER authorizes trading.

Separate from paper ledger. All writes require a valid OKX-specific lease;
the persisted status can be shown in dashboards but may never establish
current-process verification or override the live-order hard lock.
"""
import time
from google.cloud import firestore
from okx_sync import SyncStatus

class OKXSyncLedger:
    def __init__(self, db, owner_uid):
        if not isinstance(owner_uid, str) or not owner_uid or "/" in owner_uid:
            raise ValueError("Invalid owner UID")
        self.db = db
        account = db.collection("okx_readonly_accounts").document(owner_uid)
        self.lease_ref = account.collection("runtime").document("sync_lease")
        self.status_ref = account.collection("runtime").document("sync_status")

    @staticmethod
    @firestore.transactional
    def _write(tx, self, worker_id, status, now):
        lease_snap = self.lease_ref.get(transaction=tx)
        lease = lease_snap.to_dict() if lease_snap.exists else {}
        if (not worker_id or lease.get("worker_id") != worker_id
                or float(lease.get("expires_at", 0)) <= now):
            return False
        previous = self.status_ref.get(transaction=tx)
        current = previous.to_dict() if previous.exists else {}
        tx.set(self.status_ref, {
            "healthy": status.healthy,
            "reason": status.reason,
            "checked_at": status.checked_at,
            "positions_matched": status.positions_matched,
            "orders_matched": status.orders_matched,
            "sequence": int(current.get("sequence", 0)) + 1,
            "writer": worker_id,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
        return True

    def write(self, worker_id, status):
        if not isinstance(status, SyncStatus):
            raise ValueError("Invalid sync status")
        return self._write(self.db.transaction(), self, worker_id, status, time.time())

    def read_diagnostic(self):
        snapshot = self.status_ref.get()
        if not snapshot.exists:
            return None
        value = snapshot.to_dict()
        return {field: value.get(field) for field in (
            "healthy", "reason", "checked_at", "positions_matched",
            "orders_matched", "sequence", "updated_at")}
