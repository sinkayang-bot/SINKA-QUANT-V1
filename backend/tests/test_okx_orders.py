"""Order reconciliation tests; no live exchange connection."""
import pytest
from okx_orders import parse_order, reconcile, should_poll

def row(state="live", filled="0"):
    return {"ordId": "10001", "clOrdId": "sinka0001",
            "instId": "BTC-USDT-SWAP", "sz": "10",
            "accFillSz": filled, "state": state}

def test_partial_to_filled():
    initial = parse_order(row())
    partial = reconcile(initial, row("partially_filled", "4"))
    done = reconcile(partial, row("filled", "10"))
    assert should_poll(partial)
    assert not should_poll(done)

def test_cancel_after_partial():
    partial = parse_order(row("partially_filled", "4"))
    canceled = reconcile(partial, row("canceled", "4"))
    assert canceled.filled == 4
    assert not should_poll(canceled)

@pytest.mark.parametrize("invalid", [
    {"state": "unknown"},
    {"accFillSz": "-1"},
    {"accFillSz": "11"},
    {"accFillSz": "NaN"},
    {"ordId": ""},
])
def test_invalid_order_rejected(invalid):
    with pytest.raises(ValueError):
        parse_order(row() | invalid)

def test_cannot_regress_fills():
    with pytest.raises(ValueError):
        reconcile(parse_order(row("partially_filled", "5")),
                  row("partially_filled", "4"))

def test_cannot_reopen_terminal_order():
    with pytest.raises(ValueError):
        reconcile(parse_order(row("filled", "10")), row("live", "0"))

def test_order_identity_cannot_change():
    with pytest.raises(ValueError):
        reconcile(parse_order(row()), row() | {"clOrdId": "different"})
