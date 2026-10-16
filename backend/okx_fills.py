"""Bounded OKX SWAP fill scan and exact per-order quantity checks.

Read-only diagnostic: recent fills are not necessarily complete history.
No trades, account balances or trading permissions are changed.
"""
from dataclasses import dataclass
from decimal import Decimal
from okx_orders import quantity
from okx_readonly import ALLOWED_SYMBOLS

PAGE_SIZE = 100
MAX_PAGES = 20

@dataclass(frozen=True)
class Fill:
    trade_id: str
    order_id: str
    instrument: str
    side: str
    contracts: Decimal
    price: Decimal

def parse_fill(row):
    if not isinstance(row, dict):
        raise ValueError("Invalid fill record")
    instrument = row.get("instId")
    if instrument not in ALLOWED_SYMBOLS:
        raise ValueError("Unexpected fill instrument")
    trade_id, order_id = str(row.get("tradeId", "")), str(row.get("ordId", ""))
    if not trade_id.isdigit() or not order_id.isdigit():
        raise ValueError("Invalid fill identity")
    side = row.get("side")
    if side not in ("buy", "sell"):
        raise ValueError("Unknown fill side")
    contracts = quantity(row.get("fillSz"))
    price = quantity(row.get("fillPx"))
    if contracts <= 0 or price <= 0:
        raise ValueError("Invalid fill quantity or price")
    return Fill(trade_id, order_id, instrument, side, contracts, price)

async def recent_swap_fills(reader):
    """Collect up to 2000 recent fills; raise instead of silently truncating."""
    records = {}
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"instType": "SWAP", "limit": str(PAGE_SIZE)}
        if cursor is not None:
            params["after"] = cursor
        rows = await reader.get("/api/v5/trade/fills", params)
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ValueError("Invalid fills page")
        for raw in rows:
            fill = parse_fill(raw)
            if fill.trade_id in records:
                raise ValueError("Repeated fill ID across pages")
            records[fill.trade_id] = fill
        if len(rows) < PAGE_SIZE:
            return records
        cursor_next = str(rows[-1].get("billId", ""))
        if not cursor_next.isdigit() or cursor_next == cursor:
            raise ValueError("Invalid fills pagination cursor")
        cursor = cursor_next
    raise ValueError("Fill history scan safety limit reached")

def reconcile_fill_quantities(fills, tracked_orders):
    """Only verify fetched fills never exceed cumulative tracked quantities.

    A short recent-history window cannot prove completeness or a flat position.
    """
    tracked = {order.order_id: order for order in tracked_orders}
    aggregate = {}
    for fill in fills.values():
        order = tracked.get(fill.order_id)
        if order is None or order.instrument != fill.instrument:
            return False
        aggregate[fill.order_id] = aggregate.get(fill.order_id, Decimal(0)) + fill.contracts
        if aggregate[fill.order_id] > order.filled:
            return False
    return True
