"""Strict GET-only schemas for the default-off demo transport."""
from urllib.parse import urlencode
from okx_risk import SYMBOLS

ALGO_TYPES = ("conditional", "oco", "trigger", "move_order_stop", "chase")
DETAIL_FIELDS = frozenset({"ccy", "eq", "availEq", "availBal", "cashBal", "uTime"})
SCHEMAS = {
    "/api/v5/trade/order-algo": (1, frozenset({"algoId", "algoClOrdId", "instId", "instType", "state", "ordType", "sz", "side", "posSide", "tdMode", "reduceOnly", "closeFraction", "slTriggerPx", "slTriggerPxType", "slOrdPx", "tpTriggerPx", "tpTriggerPxType", "tpOrdPx", "ordIdList", "subAlgoIdList"})),
    "/api/v5/account/bills": (100, frozenset({"billId", "ts", "type", "subType", "ordId", "tradeId", "instId", "instType", "ccy", "pnl", "fee", "balChg", "posBalChg", "sz", "px"})),
    "/api/v5/account/config": (1, frozenset({"uid", "posMode", "acctLv", "perm"})),
    "/api/v5/account/balance": (1, frozenset({"details"})),
    "/api/v5/account/leverage-info": (2, frozenset({"instId", "mgnMode", "posSide", "lever"})),
    "/api/v5/public/instruments": (1, frozenset({"instId", "instType", "state", "ctType", "settleCcy",
        "ctValCcy", "ctVal", "lotSz", "minSz", "tickSz", "maxMktSz", "maxStopSz", "upcChg"})),
    "/api/v5/account/positions": (500, frozenset({"instId", "instType", "posId", "mgnMode", "posSide", "pos", "avgPx", "lever", "ccy"})),
    "/api/v5/trade/orders-pending": (100, frozenset({"instId", "instType", "ordId", "clOrdId", "state",
        "sz", "accFillSz", "side", "posSide", "tdMode", "ordType", "avgPx"})),
    "/api/v5/trade/orders-algo-pending": (100, frozenset({"algoId", "algoClOrdId", "instId", "instType",
        "state", "ordType", "sz", "side", "posSide", "tdMode", "reduceOnly", "closeFraction",
        "slTriggerPx", "slTriggerPxType", "slOrdPx", "tpTriggerPx", "tpTriggerPxType", "tpOrdPx"})),
    "/api/v5/trade/fills": (100, frozenset({"billId", "tradeId", "ordId", "instId", "side", "posSide",
        "fillSz", "fillPx", "fillTime", "fee", "feeCcy", "fillPnl"})),
}


def read_route(kind, *, symbol=None, algo_type=None, after=None):
    if symbol is not None and (not isinstance(symbol, str) or symbol not in SYMBOLS):
        raise ValueError("Instrument not allowlisted")
    if after is not None and (not isinstance(after, str) or not after.isascii() or not after.isdigit() or len(after) > 40):
        raise ValueError("Invalid read cursor")
    fixed = {
        "config": ("/api/v5/account/config", {}),
        "balance": ("/api/v5/account/balance", {"ccy":"USDT"}),
        "positions": ("/api/v5/account/positions", {}),
        "leverage": ("/api/v5/account/leverage-info", {"instId":symbol, "mgnMode":"isolated"}),
        "instrument": ("/api/v5/public/instruments", {"instType":"SWAP", "instId":symbol}),
        "orders": ("/api/v5/trade/orders-pending", {"limit":"100"}),
        "algos": ("/api/v5/trade/orders-algo-pending", {"ordType":algo_type, "limit":"100"}),
        "fills": ("/api/v5/trade/fills", {"instType":"SWAP", "limit":"100"}),
    }
    if kind not in fixed:
        raise ValueError("Read operation not permitted")
    if (kind in {"leverage", "instrument"}) != (symbol is not None):
        raise ValueError("Unexpected or missing symbol")
    if (kind == "algos" and algo_type not in ALGO_TYPES) or (kind != "algos" and algo_type is not None):
        raise ValueError("Unexpected or unsupported algo type")
    if after is not None and kind not in {"orders", "algos", "fills"}:
        raise ValueError("Unexpected pagination")
    path, params = fixed[kind]
    if after is not None:
        params["after"] = after
    return path + ("?"+urlencode(params) if params else "")


def safe_row(row, fields):
    if not isinstance(row, dict):
        raise ValueError("Malformed response row")
    safe = {}
    for key in fields & row.keys():
        value = row[key]
        if key in {"details", "upcChg"}:
            nested_fields = DETAIL_FIELDS if key == "details" else frozenset({"param", "newValue", "effTime"})
            if not isinstance(value, list) or len(value) > 100:
                raise ValueError("Malformed nested response")
            safe[key] = [safe_row(item, nested_fields) for item in value]
        elif key in {"ordIdList", "subAlgoIdList"}:
            if (not isinstance(value,list) or len(value)>10
                    or any(not isinstance(item,str) or not item.isascii() or not item.isdigit()
                           or not 1<=len(item)<=40 or item.startswith("0") for item in value)):
                raise ValueError("Malformed child identities")
            safe[key]=list(value)
        elif key == "reduceOnly" and type(value) is bool:
            safe[key] = value
        elif isinstance(value, str) and len(value) <= 128:
            safe[key] = value
        else:
            raise ValueError("Malformed response value")
    return safe


def bill_route(begin_ms, end_ms, *, after=None):
    """One account-wide currency interval; no instrument/type filters."""
    if (type(begin_ms) is not int or type(end_ms) is not int
            or not 0 < begin_ms <= end_ms <= 253402300799000 or end_ms-begin_ms>604800000):
        raise ValueError("Bounded bill interval required")
    params={"ccy":"USDT","begin":str(begin_ms),"end":str(end_ms),"limit":"100"}
    if after is not None:
        if not isinstance(after,str) or not after.isascii() or not after.isdigit() or not 1<=len(after)<=40 or after.startswith("0"):
            raise ValueError("Invalid bill cursor")
        params["after"]=after
    return "/api/v5/account/bills?"+urlencode(params)
