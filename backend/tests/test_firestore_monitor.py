"""Tests for the Firestore paper monitor decision logic."""
import asyncio
from unittest.mock import AsyncMock
import firestore_monitor

class Ledger:
    def __init__(self, position):
        self.position = position
        self.closed = []
    def snapshot(self):
        return {"position": self.position}
    def close(self, price, reason, position_id):
        self.closed.append((price, position_id))
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
