"""Mocked-only fill scan tests; never reach OKX."""
import asyncio
import pytest
from okx_fills import parse_fill, recent_swap_fills, reconcile_fill_quantities
from okx_orders import parse_order

def row(i, order_id="123"):
    return {"tradeId": str(i), "billId": str(9000 - int(i)),
            "ordId": order_id, "instId": "BTC-USDT-SWAP",
            "side": "buy", "fillSz": "2", "fillPx": "100000"}

class Reader:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []
    async def get(self, path, params):
        self.calls.append((path, params))
        return next(self.pages)

def test_empty_fills():
    assert asyncio.run(recent_swap_fills(Reader([[]]))) == {}

def test_parse_valid_fill():
    assert parse_fill(row(1)).contracts == 2

@pytest.mark.parametrize("change", [
    {"tradeId": ""}, {"ordId": ""}, {"fillSz": "0"},
    {"fillSz": "NaN"}, {"fillPx": "0"}, {"side": "hold"},
    {"instId": "LTC-USDT-SWAP"},
])
def test_reject_invalid_fills(change):
    with pytest.raises(ValueError):
        parse_fill(row(1) | change)

def test_duplicate_fill_id_rejected():
    with pytest.raises(ValueError, match="Repeated"):
        asyncio.run(recent_swap_fills(Reader([[row(1), row(1)]])))

def test_two_pages_have_cursor():
    first = [row(i) for i in range(100)]
    client = Reader([first, [row(100)]])
    fills = asyncio.run(recent_swap_fills(client))
    assert len(fills) == 101
    assert client.calls[1][1]["after"] == first[-1]["billId"]

def test_fills_cannot_exceed_order_cumulative_qty():
    order = parse_order({"ordId": "123", "clOrdId": "sinka123",
                         "instId": "BTC-USDT-SWAP", "state": "partially_filled",
                         "sz": "10", "accFillSz": "3"})
    fills = {"1": parse_fill(row(1)), "2": parse_fill(row(2))}
    assert not reconcile_fill_quantities(fills, [order])
    assert reconcile_fill_quantities({"1": parse_fill(row(1))}, [order])
    assert not reconcile_fill_quantities({"1": parse_fill(row(1, "999"))}, [order])
