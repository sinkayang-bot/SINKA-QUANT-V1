"""Mocked OKX sync checks. No exchange access."""
import asyncio
from unittest.mock import AsyncMock
from okx_sync import sync_once
from okx_orders import parse_order

def reader(positions=None, fail=False):
    class Fake:
        async def swap_positions(self):
            if fail:
                raise ConnectionError("network unavailable")
            return [] if positions is None else positions
        refresh = AsyncMock(side_effect=lambda order: order)
        async def get(self, path, params):
            assert path == '/api/v5/trade/orders-pending'
            return []
    return Fake()

def test_flat_synced():
    status = asyncio.run(sync_once(reader=reader(), local_position=None))
    assert status.healthy and status.positions_matched and status.orders_matched

def test_unknown_position_blocks():
    remote = [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
               "posSide": "long", "pos": "1"}]
    status = asyncio.run(sync_once(reader=reader(remote), local_position=None))
    assert not status.healthy
    assert status.reason == "exchange_position_not_tracked"

def test_network_failure_blocks():
    status = asyncio.run(sync_once(reader=reader(fail=True), local_position=None))
    assert not status.healthy
    assert status.reason == "exchange_sync_failed"

def test_active_orders_refreshed():
    prior = parse_order({"ordId": "123", "clOrdId": "sinka123",
                         "instId": "BTC-USDT-SWAP", "state": "live",
                         "sz": "1", "accFillSz": "0"})
    obj = reader()
    status = asyncio.run(sync_once(reader=obj, local_position=None,
                                   tracked_orders=[prior]))
    assert not status.healthy
    assert status.reason == 'pending_order_mismatch'
    obj.refresh.assert_awaited_once_with(prior)


def test_empty_verified_inventory_is_healthy():
    status = asyncio.run(sync_once(reader=reader(), local_position=None))
    assert status.healthy
    assert status.positions_matched and status.orders_matched

def test_verified_partial_fill_is_persisted():
    from okx_orders import parse_order
    prior = parse_order({"ordId": "456", "clOrdId": "sinka456",
                         "instId": "BTC-USDT-SWAP", "state": "live",
                         "sz": "10", "accFillSz": "0"})
    partial = parse_order({"ordId": "456", "clOrdId": "sinka456",
                           "instId": "BTC-USDT-SWAP", "state": "partially_filled",
                           "sz": "10", "accFillSz": "4"})
    obj = reader()
    obj.refresh = AsyncMock(return_value=partial)
    obj.get = AsyncMock(return_value=[{"ordId": "456", "clOrdId": "sinka456",
                                      "instId": "BTC-USDT-SWAP",
                                      "state": "partially_filled", "sz": "10",
                                      "accFillSz": "4"}])
    persist = AsyncMock(return_value=True)
    result = asyncio.run(sync_once(reader=obj, local_position=None,
                                   tracked_orders=[prior], persist_verified=persist))
    assert not result.healthy
    assert result.reason == "filled_order_requires_position_reconciliation"
    persist.assert_awaited_once_with(partial)

def test_rejected_journal_write_fails_closed():
    prior = parse_order({"ordId": "456", "clOrdId": "sinka456",
                         "instId": "BTC-USDT-SWAP", "state": "live",
                         "sz": "10", "accFillSz": "0"})
    filled = parse_order({"ordId": "456", "clOrdId": "sinka456",
                          "instId": "BTC-USDT-SWAP", "state": "filled",
                          "sz": "10", "accFillSz": "10"})
    obj = reader()
    obj.refresh = AsyncMock(return_value=filled)
    persist = AsyncMock(return_value=False)
    result = asyncio.run(sync_once(reader=obj, local_position=None,
                                   tracked_orders=[prior], persist_verified=persist))
    assert not result.healthy and result.reason == "journal_write_denied"

def test_pending_mismatch_never_persists():
    prior = parse_order({"ordId": "123", "clOrdId": "sinka123",
                         "instId": "BTC-USDT-SWAP", "state": "live",
                         "sz": "10", "accFillSz": "0"})
    obj = reader()
    persist = AsyncMock(return_value=True)
    result = asyncio.run(sync_once(reader=obj, local_position=None,
                                   tracked_orders=[prior], persist_verified=persist))
    assert not result.healthy
    persist.assert_not_awaited()

def test_missing_persistence_blocks_transition():
    previous = parse_order({"ordId": "999", "clOrdId": "sinka999",
                            "instId": "BTC-USDT-SWAP", "state": "live",
                            "sz": "10", "accFillSz": "0"})
    updated = parse_order({"ordId": "999", "clOrdId": "sinka999",
                           "instId": "BTC-USDT-SWAP", "state": "filled",
                           "sz": "10", "accFillSz": "10"})
    obj = reader()
    obj.refresh = AsyncMock(return_value=updated)
    result = asyncio.run(sync_once(reader=obj, local_position=None,
                                   tracked_orders=[previous]))
    assert result.reason == "journal_persistence_unavailable"
    assert not result.healthy

def test_changed_positions_during_poll_fail_closed():
    class Race:
        calls = 0
        async def swap_positions(self):
            self.calls += 1
            if self.calls == 1:
                return []
            return [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
                     "posSide": "long", "pos": "1"}]
        async def get(self, path, params):
            return []
    status = asyncio.run(sync_once(reader=Race(), local_position=None))
    assert not status.healthy
    assert status.reason == "position_changed_during_sync"

def test_changed_pending_orders_during_poll_fail_closed():
    class Race:
        calls = 0
        async def swap_positions(self):
            return []
        async def get(self, path, params):
            self.calls += 1
            if self.calls == 1:
                return []
            return [{"ordId": "100", "clOrdId": "sinka100",
                     "instId": "BTC-USDT-SWAP", "state": "live",
                     "sz": "1", "accFillSz": "0"}]
    status = asyncio.run(sync_once(reader=Race(), local_position=None))
    assert not status.healthy
    assert status.reason == "orders_changed_during_sync"

def test_old_filled_order_cannot_prove_flat_account_safe():
    past = parse_order({"ordId": "712", "clOrdId": "sinka712",
                        "instId": "BTC-USDT-SWAP", "state": "filled",
                        "sz": "5", "accFillSz": "5"})
    result = asyncio.run(sync_once(reader=reader(), local_position=None,
                                   tracked_orders=[past]))
    assert not result.healthy
    assert result.reason == "filled_order_requires_position_reconciliation"

def test_cancelled_without_fill_can_be_checked_when_flat():
    canceled = parse_order({"ordId": "713", "clOrdId": "sinka713",
                            "instId": "BTC-USDT-SWAP", "state": "canceled",
                            "sz": "5", "accFillSz": "0"})
    result = asyncio.run(sync_once(reader=reader(), local_position=None,
                                   tracked_orders=[canceled]))
    assert result.healthy
