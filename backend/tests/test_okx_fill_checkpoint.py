"""No-network restart overlap tests for OKX fill checkpoints."""
from dataclasses import replace
from okx_fills import parse_fill
from okx_fill_checkpoint import FillCheckpoint, reconcile_fill_window

def fill(i):
    return parse_fill({"tradeId": str(i), "ordId": "100",
                       "instId": "BTC-USDT-SWAP", "side": "buy",
                       "fillSz": "1", "fillPx": "100000"})

def anchor(i):
    f = fill(i)
    return FillCheckpoint(f.trade_id, f)

def test_overlap_yields_only_new_fills_oldest_first():
    result = reconcile_fill_window(anchor(10), [fill(13), fill(12), fill(11), fill(10), fill(9)])
    assert result.safe_to_append
    assert [f.trade_id for f in result.unseen] == ["11", "12", "13"]
    assert result.next_checkpoint.anchor_id == "13"
    assert result.history_complete is False

def test_repeated_poll_does_not_duplicate_fills():
    result = reconcile_fill_window(anchor(10), [fill(10), fill(9)])
    assert result.safe_to_append
    assert result.unseen == ()

def test_missing_anchor_fails_closed():
    result = reconcile_fill_window(anchor(10), [fill(15), fill(14)])
    assert not result.safe_to_append
    assert result.reason == "checkpoint_not_in_window"
    assert result.next_checkpoint == anchor(10)

def test_duplicate_ids_fails_closed():
    result = reconcile_fill_window(anchor(10), [fill(11), fill(11), fill(10)])
    assert not result.safe_to_append
    assert result.reason == "duplicate_fill_id"

def test_mutated_anchor_fails_closed():
    tampered = replace(fill(10), price=fill(10).price + 1)
    result = reconcile_fill_window(anchor(10), [fill(11), tampered])
    assert not result.safe_to_append
    assert result.reason == "checkpoint_content_changed"

def test_new_account_requires_verified_initial_baseline():
    result = reconcile_fill_window(None, [fill(10)])
    assert not result.safe_to_append
    assert result.reason == "initial_baseline_required"

def test_empty_window_does_not_erase_previous_checkpoint():
    result = reconcile_fill_window(anchor(10), [])
    assert not result.safe_to_append
    assert result.next_checkpoint == anchor(10)
