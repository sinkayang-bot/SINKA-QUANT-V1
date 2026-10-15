"""Firestore-backed paper ledger prototype (no real exchange orders).

Designed for Cloud Run migration. The public API still uses SQLite until integration
and emulator tests pass. All mutating operations use Firestore transactions.
"""
import math
import time
from google.cloud import firestore

INITIAL = 10000.0
FEE = 0.0005
SLIP = 0.0002
RISK = 0.005
LEVERAGE = 3.0


class PaperLedger:
    def __init__(self, db=None, account_id="default"):
        self.db = db or firestore.Client()
        self.account = self.db.collection("paper_accounts").document(account_id)

    def _ensure(self, transaction):
        snapshot = self.account.get(transaction=transaction)
        if not snapshot.exists:
            transaction.create(self.account, {
                "cash": INITIAL, "position": None, "version": 0,
                "created_at": firestore.SERVER_TIMESTAMP,
            })
            return {"cash": INITIAL, "position": None, "version": 0}
        return snapshot.to_dict()

    @firestore.transactional
    def _initialize(self, transaction):
        return self._ensure(transaction)

    def initialize(self):
        return self._initialize(self.db.transaction())

    @firestore.transactional
    def _open(self, transaction, symbol, side, entry, stop, key):
        request_ref = self.account.collection("requests").document(key)
        request_used = request_ref.get(transaction=transaction).exists
        account_snapshot = self.account.get(transaction=transaction)
        if not account_snapshot.exists:
            raise ValueError("Paper account not initialized")
        state = account_snapshot.to_dict()
        if request_used:
            raise ValueError("Duplicate request ID")
        if state.get("position"):
            raise ValueError("A position is already open")
        cash = float(state["cash"])
        distance = side * (entry - stop)
        if not all(math.isfinite(x) for x in (cash, distance)) or cash <= 0 or distance <= 0:
            raise ValueError("Invalid risk or stop")
        qty = min(cash * RISK / (distance + entry * FEE + stop * FEE), cash * LEVERAGE / entry)
        fee = qty * entry * FEE
        if qty <= 0 or fee >= cash:
            raise ValueError("Insufficient paper balance")
        position = {"symbol": symbol, "side": side, "entry": entry, "stop": stop,
                    "qty": qty, "opened_at": int(time.time()), "id": key}
        transaction.create(request_ref, {"created_at": firestore.SERVER_TIMESTAMP, "type": "open"})
        transaction.update(self.account, {"cash": cash - fee, "position": position,
                                          "version": state["version"] + 1})
        return position

    def open(self, symbol, side, observed_price, stop, request_id):
        if not isinstance(request_id, str) or not (8 <= len(request_id) <= 128):
            raise ValueError("Invalid request ID")
        if symbol not in {"BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP"}:
            raise ValueError("Unsupported instrument")
        if side not in (1, -1):
            raise ValueError("Invalid side")
        if not all(math.isfinite(x) and x > 0 for x in (observed_price, stop)):
            raise ValueError("Invalid price")
        entry = observed_price * (1 + side * SLIP)
        return self._open(self.db.transaction(), symbol, side, entry, stop, request_id)

    @firestore.transactional
    def _close(self, transaction, observed_price, reason, expected_id, worker_id=None):
        snapshot = self.account.get(transaction=transaction)
        if worker_id is not None:
            lease_ref = self.account.collection("runtime").document("lease")
            lease_snap = lease_ref.get(transaction=transaction)
            lease = lease_snap.to_dict() if lease_snap.exists else {}
            if lease.get("worker_id") != worker_id or float(lease.get("expires_at", 0)) <= time.time():
                return None
        if not snapshot.exists:
            return None
        state = snapshot.to_dict()
        position = state.get("position")
        if not position or position["id"] != expected_id:
            return None  # Already closed or replaced; idempotent stop handling.
        side = position["side"]
        qty = position["qty"]
        exit_price = observed_price * (1 - side * SLIP)
        gross = qty * side * (exit_price - position["entry"])
        exit_fee = qty * exit_price * FEE
        entry_fee = qty * position["entry"] * FEE
        pnl = gross - exit_fee - entry_fee
        fill = {"position_id": expected_id, "symbol": position["symbol"], "side": side,
                "qty": qty, "entry": position["entry"], "exit": exit_price,
                "pnl": pnl, "reason": reason, "closed_at": firestore.SERVER_TIMESTAMP}
        transaction.create(self.account.collection("fills").document(expected_id), fill)
        transaction.update(self.account, {"cash": state["cash"] + gross - exit_fee,
                                          "position": None, "version": state["version"] + 1})
        return fill

    def close(self, observed_price, reason, expected_id, worker_id=None):
        if not math.isfinite(observed_price) or observed_price <= 0:
            raise ValueError("Invalid price")
        return self._close(self.db.transaction(), observed_price, reason, expected_id, worker_id)

    def snapshot(self):
        snap = self.account.get()
        return snap.to_dict() if snap.exists else None
