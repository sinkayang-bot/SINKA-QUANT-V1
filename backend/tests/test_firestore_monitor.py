"""Tests for the Firestore paper monitor decision logic."""
import asyncio
from unittest.mock import AsyncMock
import firestore_monitor

class Ledger:
    def __init__(self, position):
        self.position = position
        self.closed = []
        self.close_workers = []
    def snapshot(self):
        return {"position": self.position}
    def close(self, price, reason, position_id, worker_id=None):
        self.closed.append((price, position_id))
        self.close_workers.append(worker_id)
        return {"position_id": position_id}

def test_idle():
    assert asyncio.run(firestore_monitor.check_once(Ledger(None))) == "idle"

def test_long_stop(monkeypatch):
    ledger = Ledger({"id": "test-position", "symbol": "BTC-USDT-SWAP", "side": 1, "stop": 99000})
    monkeypatch.setattr(firestore_monitor, "quote", AsyncMock(return_value=98900))
    assert asyncio.run(firestore_monitor.check_once(ledger)) == "closed"
    assert ledger.closed == [(98900, "test-position")]

def test_short_not_triggered(monkeypatch):
    ledger = Ledger({"id": "test-position", "symbol": "BTC-USDT-SWAP", "side": -1, "stop": 101000})
    monkeypatch.setattr(firestore_monitor, "quote", AsyncMock(return_value=100000))
    assert asyncio.run(firestore_monitor.check_once(ledger)) == "watching"
    assert ledger.closed == []

def test_lease_lost_prevents_close(monkeypatch):
    ledger = Ledger({"id": "test-position", "symbol": "BTC-USDT-SWAP", "side": 1, "stop": 99000})
    monkeypatch.setattr(firestore_monitor, "quote", AsyncMock(return_value=98900))
    class LostLease:
        worker_id = "worker-test"
        def is_owner(self):
            return False
    assert asyncio.run(firestore_monitor.check_once(ledger, lease=LostLease())) == "lease_lost"
    assert ledger.closed == []


def test_short_stop(monkeypatch):
    ledger = Ledger({"id": "short-position", "symbol": "ETH-USDT-SWAP", "side": -1, "stop": 101000})
    monkeypatch.setattr(firestore_monitor, "quote", AsyncMock(return_value=101500))
    assert asyncio.run(firestore_monitor.check_once(ledger)) == "closed"
    assert ledger.closed == [(101500, "short-position")]


def test_long_stop_not_triggered(monkeypatch):
    ledger = Ledger({"id": "long-position", "symbol": "BTC-USDT-SWAP", "side": 1, "stop": 99000})
    monkeypatch.setattr(firestore_monitor, "quote", AsyncMock(return_value=100000))
    assert asyncio.run(firestore_monitor.check_once(ledger)) == "watching"
    assert ledger.closed == []


def test_lease_owner_is_forwarded_to_close(monkeypatch):
    ledger = Ledger({"id": "leased-position", "symbol": "BTC-USDT-SWAP", "side": 1, "stop": 99000})
    monkeypatch.setattr(firestore_monitor, "quote", AsyncMock(return_value=98000))
    class Lease:
        worker_id = "lease-owner"
        def is_owner(self):
            return True
    assert asyncio.run(firestore_monitor.check_once(ledger, lease=Lease())) == "closed"
    assert ledger.closed == [(98000, "leased-position")]
    assert ledger.close_workers == ["lease-owner"]
