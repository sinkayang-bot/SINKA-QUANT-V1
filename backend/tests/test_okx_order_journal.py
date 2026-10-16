"""Local order journal serialization tests, no Firestore service needed."""
import pytest
from okx_orders import parse_order
from okx_order_journal import encode_order, decode_order

def order(state="partially_filled", filled="2"):
    return parse_order({
        "ordId": "12345678", "clOrdId": "sinka-12345678",
        "instId": "BTC-USDT-SWAP", "state": state,
        "sz": "10", "accFillSz": filled,
    })

def test_round_trip_preserves_decimal_quantity():
    original = order()
    assert decode_order(encode_order(original)) == original

def test_does_not_silently_accept_unallowlisted_instrument():
    original = order()
    from dataclasses import replace
    with pytest.raises(ValueError, match="Unsupported"):
        encode_order(replace(original, instrument="LTC-USDT-SWAP"))

def test_terminal_orders_round_trip():
    original = order("filled", "10")
    assert decode_order(encode_order(original)) == original
