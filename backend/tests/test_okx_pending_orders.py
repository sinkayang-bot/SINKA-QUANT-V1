"""Mock-only pending-order inventory tests; no network or credentials."""
import asyncio
import pytest
from okx_orders import parse_order
from okx_pending_orders import pending_swap_inventory, verify_pending_inventory

def order(order_id, symbol="BTC-USDT-SWAP"):
    return {"ordId": str(order_id), "clOrdId": "sinka" + str(order_id),
            "instId": symbol, "state": "live", "sz": "1", "accFillSz": "0"}

class Reader:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []
    async def get(self, path, params):
        self.calls.append((path, params))
        return next(self.pages)

def test_empty_pending_inventory():
    result = asyncio.run(pending_swap_inventory(Reader([[]])))
    assert result == {}
    assert verify_pending_inventory(result, [])

def test_pending_inventory_matches_only_exact_tracked_set():
    snapshot = parse_order(order(123))
    found = asyncio.run(pending_swap_inventory(Reader([[order(123)]])))
    assert verify_pending_inventory(found, [snapshot])
    assert not verify_pending_inventory(found, [])
    assert not verify_pending_inventory(found, [snapshot, snapshot])

def test_paginated_inventory():
    first = [order(200 - i) for i in range(100)]
    second = [order(100)]
    client = Reader([first, second])
    result = asyncio.run(pending_swap_inventory(client))
    assert len(result) == 101
    assert client.calls[1][1]["after"] == "101"

@pytest.mark.parametrize("pages", [
    [[order(1, "LTC-USDT-SWAP")]],
    [[order(1), order(1)]],
    [[{"ordId": "1", "instId": "BTC-USDT-SWAP", "state": "live", "sz": "1"}]],
])
def test_unsafe_inventory_fails_closed(pages):
    with pytest.raises(ValueError):
        asyncio.run(pending_swap_inventory(Reader(pages)))

def test_pending_order_changed_state_fails_comparison():
    found = asyncio.run(pending_swap_inventory(Reader([[order(1)]])))
    assert not verify_pending_inventory(found, [parse_order(order(1) | {"state": "partially_filled"})])

def test_reused_client_order_id_rejected():
    first = order(100)
    second = order(101)
    second["clOrdId"] = first["clOrdId"]
    with pytest.raises(ValueError, match="Repeated"):
        asyncio.run(pending_swap_inventory(Reader([[first, second]])))
