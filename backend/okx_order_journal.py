"""Durable, read-only OKX order journal with lease-fenced reconciliation.

Only previously registered order IDs can be refreshed. Unknown exchange
orders are NEVER silently adopted. No order submission or trading authority.
"""
import time
from decimal import Decimal
from google.cloud import firestore
from okx_orders import OrderSnapshot, ACTIVE, reconcile
from okx_readonly import ALLOWED_SYMBOLS

MAX_TRACKED_ORDERS = 500

def encode_order(order):
    if not isinstance(order, OrderSnapshot):
        raise ValueError("Order snapshot required")
    if order.instrument not in ALLOWED_SYMBOLS:
        raise ValueError("Unsupported swap instrument")
    if not (order.order_id.isdigit() and order.client_order_id
            and "/" not in order.client_order_id):
        raise ValueError("Invalid order identifiers")
    if order.requested <= 0 or order.filled < 0 or order.filled > order.requested:
        raise ValueError("Invalid order quantity")
    return {
        "ordId": order.order_id, "clOrdId": order.client_order_id,
        "instId": order.instrument, "state": order.state,
        "sz": str(order.requested), "accFillSz": str(order.filled),
    }

def decode_order(doc):
    from okx_orders import parse_order
    return parse_order(doc)

class OKXOrderJournal:
    def __init__(self, sync_ledger):
        self.db = sync_ledger.db
        self.lease_ref = sync_ledger.lease_ref
        self.orders = self.lease_ref.parent.parent.collection("tracked_orders")

    def _owner(self, tx, worker_id, now):
        lease_snap = self.lease_ref.get(transaction=tx)
        data = lease_snap.to_dict() if lease_snap.exists else {}
        return bool(worker_id) and data.get("worker_id") == worker_id and (
            float(data.get("expires_at", 0)) > now)

    @firestore.transactional
    def _register(self, tx, worker_id, order, now):
        if not self._owner(tx, worker_id, now):
            return False
        ref = self.orders.document(order.order_id)
        if ref.get(transaction=tx).exists:
            raise ValueError("Order already registered")
        tx.create(ref, {**encode_order(order),
                        "updated_at": firestore.SERVER_TIMESTAMP})
        return True

    def register_existing(self, worker_id, order):
        """Explicit recovery import only; no automatic order adoption."""
        encode_order(order)
        return self._register(self.db.transaction(), worker_id, order, time.time())

    @firestore.transactional
    def _refresh(self, tx, worker_id, order, now):
        if not self._owner(tx, worker_id, now):
            return False
        ref = self.orders.document(order.order_id)
        snap = ref.get(transaction=tx)
        if not snap.exists:
            raise ValueError("Untracked order; do not adopt automatically")
        previous = decode_order(snap.to_dict())
        next_order = reconcile(previous, encode_order(order))
        if next_order != previous:
            tx.update(ref, {**encode_order(next_order),
                            "updated_at": firestore.SERVER_TIMESTAMP})
        return True

    def refresh(self, worker_id, order):
        encode_order(order)
        return self._refresh(self.db.transaction(), worker_id, order, time.time())

    def list_tracked(self):
        """Fail closed when the bounded diagnostic journal grows too large."""
        documents = list(self.orders.limit(MAX_TRACKED_ORDERS + 1).stream())
        if len(documents) > MAX_TRACKED_ORDERS:
            raise ValueError("Order journal exceeds safe scan limit")
        result = [decode_order(doc.to_dict()) for doc in documents]
        if len({order.order_id for order in result}) != len(result):
            raise ValueError("Duplicate order IDs")
        return result

    def list_active(self):
        return [order for order in self.list_tracked() if order.state in ACTIVE]
