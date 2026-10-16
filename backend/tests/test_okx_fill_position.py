"""Pure reconciliation tests; no exchange requests or trade side effects."""
import pytest
from decimal import Decimal
from okx_fills import parse_fill
from okx_fill_position import reconcile_net_fill_interval

def fill(i, side, qty):
    return parse_fill({
        "tradeId": str(i), "ordId": str(1000 + i),
        "instId": "BTC-USDT-SWAP", "side": side,
        "fillSz": str(qty), "fillPx": "100000",
    })

def evaluate(start, events, final, complete=True):
    return reconcile_net_fill_interval(
        symbol="BTC-USDT-SWAP", starting_contracts=start, fills=events,
        observed_contracts=final, interval_complete=complete)

def test_open_increase_reduce_close():
    result = evaluate("0", [fill(1,"buy",3), fill(2,"buy",2),
                            fill(3,"sell",4), fill(4,"sell",1)], "0")
    assert result.matched
    assert result.transitions == ("open","increase","reduce","close")

def test_short_open_partial_cover_and_close():
    result = evaluate("0", [fill(1,"sell",5), fill(2,"buy",2),
                            fill(3,"buy",3)], "0")
    assert result.matched
    assert result.transitions == ("open","reduce","close")

def test_open_position_with_missing_history_is_never_verified():
    result = evaluate("0", [fill(1,"buy",2)], "2", complete=False)
    assert not result.matched
    assert result.reason == "fill_history_incomplete"

def test_mismatch_detected():
    result = evaluate("0", [fill(1,"buy",2)], "1")
    assert not result.matched
    assert result.expected == Decimal("2")

def test_reversal_requires_review():
    result = evaluate("2", [fill(1,"sell",3)], "-1")
    assert not result.matched
    assert result.reason == "position_reversal_requires_review"

def test_duplicate_fill_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate("0", [fill(1,"buy",1), fill(1,"buy",1)], "2")

def test_bad_starting_qty_rejected():
    with pytest.raises(ValueError):
        evaluate("NaN", [], "0")

def test_nonzero_verified_baseline():
    result = evaluate("-4", [fill(1,"buy",1)], "-3")
    assert result.matched
    assert result.transitions == ("reduce",)
