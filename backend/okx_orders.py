"""Pure OKX order-state reconciliation. No exchange order submission."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

TERMINAL = frozenset({"filled", "canceled", "mmp_canceled"})
ACTIVE = frozenset({"live", "partially_filled"})
KNOWN = TERMINAL | ACTIVE

def quantity(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid fill quantity") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid fill quantity")
    return result

@dataclass(frozen=True)
class OrderSnapshot:
    order_id: str
    client_order_id: str
    instrument: str
    state: str
    requested: Decimal
    filled: Decimal

def parse_order(row):
    if not isinstance(row, dict):
        raise ValueError("Invalid order record")
    state = row.get("state")
    if state not in KNOWN:
        raise ValueError("Unknown OKX order state")
    requested = quantity(row.get("sz"))
    filled = quantity(row.get("accFillSz", "0"))
    if requested <= 0 or filled > requested:
        raise ValueError("Inconsistent order quantities")
    order_id = str(row.get("ordId", "")).strip()
    client_id = str(row.get("clOrdId", "")).strip()
    if not order_id or not client_id:
        raise ValueError("Order identifiers required")
    return OrderSnapshot(order_id, client_id, str(row.get("instId", "")),
                         state, requested, filled)

def reconcile(previous, incoming):
    current = parse_order(incoming)
    if previous is None:
        return current
    if (previous.order_id != current.order_id or
            previous.client_order_id != current.client_order_id or
            previous.instrument != current.instrument or
            previous.requested != current.requested):
        raise ValueError("Order identity changed")
    if previous.state in TERMINAL and current != previous:
        raise ValueError("Terminal order changed")
    if current.filled < previous.filled:
        raise ValueError("Filled quantity went backwards")
    if previous.state == "partially_filled" and current.state == "live":
        raise ValueError("Order state went backwards")
    return current

def should_poll(snapshot):
    return snapshot.state not in TERMINAL
