"""Tests for safe OKX position snapshot normalization."""
import pytest
from okx_position_journal import normalize_positions

def test_empty_positions():
    assert normalize_positions([]) == []

def test_signed_net_short():
    assert normalize_positions([{"instId": "BTC-USDT-SWAP",
        "mgnMode": "isolated", "posSide": "net", "pos": "-2"}]) == [
        {"symbol": "BTC-USDT-SWAP", "posSide": "net",
         "contracts": "-2", "marginMode": "isolated"}]

@pytest.mark.parametrize("rows", [
    None, [{"instId": "BTC-USDT-SWAP", "mgnMode": "cross",
             "posSide": "net", "pos": "1"}],
    [{"instId": "LTC-USDT-SWAP", "mgnMode": "isolated",
      "posSide": "net", "pos": "1"}],
    [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
      "posSide": "long", "pos": "-1"}],
    [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
      "posSide": "net", "pos": "NaN"}],
])
def test_reject_bad_snapshots(rows):
    with pytest.raises(ValueError):
        normalize_positions(rows)

def test_multiple_positions_block_snapshot():
    rows = [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
             "posSide": "net", "pos": "1"},
            {"instId": "ETH-USDT-SWAP", "mgnMode": "isolated",
             "posSide": "net", "pos": "1"}]
    with pytest.raises(ValueError):
        normalize_positions(rows)
