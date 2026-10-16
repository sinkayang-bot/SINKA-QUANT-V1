"""Deterministic NET-mode fill-to-position accounting (diagnostic only).

Requires a separately verified starting quantity and a COMPLETE chronologically
ordered fill interval. Recent OKX fills alone cannot establish completeness.
Does not trade, assume a flat starting balance, or unlock any admission gate.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from okx_fills import Fill
from okx_readonly import ALLOWED_SYMBOLS

@dataclass(frozen=True)
class PositionReconciliation:
    matched: bool
    reason: str
    expected: Decimal | None
    observed: Decimal | None
    transitions: tuple[str, ...]

def _signed(value):
    try:
        x = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid signed position quantity") from exc
    if not x.is_finite():
        raise ValueError("Invalid signed position quantity")
    return x

def reconcile_net_fill_interval(*, symbol, starting_contracts,
                                fills, observed_contracts,
                                interval_complete=False):
    """Account for buys/sells without guessing unobserved earlier fills.

    Callers must guarantee sorted, gap-free events for the SAME net-mode
    instrument and an independently verified opening balance.
    """
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError("Unsupported instrument")
    start = _signed(starting_contracts)
    observed = _signed(observed_contracts)
    if not interval_complete:
        return PositionReconciliation(False, "fill_history_incomplete",
                                      None, observed, ())
    current = start
    seen = set()
    transitions = []
    for fill in fills:
        if not isinstance(fill, Fill) or fill.instrument != symbol:
            raise ValueError("Unexpected fill instrument")
        if fill.trade_id in seen:
            raise ValueError("Duplicate trade ID")
        seen.add(fill.trade_id)
        change = fill.contracts if fill.side == "buy" else -fill.contracts
        if change == 0:
            raise ValueError("Zero fill quantity")
        following = current + change
        if current == 0:
            transitions.append("open")
        elif following == 0:
            transitions.append("close")
        elif (current > 0) != (following > 0):
            return PositionReconciliation(False, "position_reversal_requires_review",
                                          following, observed, tuple(transitions))
        elif abs(following) > abs(current):
            transitions.append("increase")
        else:
            transitions.append("reduce")
        current = following
    if current != observed:
        return PositionReconciliation(False, "position_quantity_mismatch",
                                      current, observed, tuple(transitions))
    return PositionReconciliation(True, "net_fills_match_position",
                                  current, observed, tuple(transitions))
