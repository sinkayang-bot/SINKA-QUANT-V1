"""Read-only, bounded OKX pending SWAP order inventory.

Fail closed on pagination anomalies, unknown instruments, or invalid records.
A returned inventory is a moment-in-time snapshot, not a trading authorization.
"""
from okx_readonly import ALLOWED_SYMBOLS
from okx_orders import parse_order, ACTIVE

PAGE_SIZE = 100
MAX_PAGES = 20

async def pending_swap_inventory(reader):
    found = {}
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"instType": "SWAP", "limit": str(PAGE_SIZE)}
        if cursor is not None:
            params["after"] = cursor
        rows = await reader.get("/api/v5/trade/orders-pending", params)
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ValueError("Invalid pending-order page")
        for row in rows:
            if not isinstance(row, dict) or row.get("instId") not in ALLOWED_SYMBOLS:
                raise ValueError("Unrecognized pending SWAP exposure")
            record = parse_order(row)
            if record.state not in ACTIVE:
                raise ValueError("Unexpected terminal order in pending list")
            if record.order_id in found:
                raise ValueError("Repeated pending order across pages")
            found[record.order_id] = record
        if len(rows) < PAGE_SIZE:
            return found
        last_id = str(rows[-1].get("ordId", ""))
        if not last_id.isdigit() or last_id == cursor:
            raise ValueError("Invalid pending-order pagination cursor")
        cursor = last_id
    raise ValueError("Pending order inventory exceeds scan limit")

def verify_pending_inventory(inventory, tracked_orders):
    """Require all live orders to be tracked with matching quantity and state."""
    if not isinstance(inventory, dict):
        return False
    tracked = {}
    try:
        for order in tracked_orders:
            if order.state in ACTIVE:
                if order.order_id in tracked:
                    return False
                tracked[order.order_id] = order
        if set(inventory) != set(tracked):
            return False
        return all(inventory[k] == tracked[k] for k in inventory)
    except (ValueError, AttributeError, TypeError):
        return False
