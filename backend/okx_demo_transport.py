"""Default-off OKX demo-only request transport. Not wired to any runtime.

No credential discovery, live mode, custom host, generic request API or retries.
Tests inject httpx.MockTransport; they never contact an exchange.
"""
import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlencode

import httpx
from okx_risk import SYMBOLS

ORIGIN = "https://www.okx.com"
MAX_RESPONSE_BYTES = 65536
TIMEOUT_SECONDS = 5.0
DEMO_HEADERS = {"x-simulated-trading": "1"}
ACK_FIELDS = frozenset({"sCode", "ordId", "clOrdId"})
ORDER_FIELDS = frozenset({"ordId", "clOrdId", "instId", "state", "sz", "accFillSz",
    "side", "posSide", "tdMode", "ordType", "avgPx", "fee", "feeCcy", "fillPx"})


class DemoTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DemoCredentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(repr=False)

    def __post_init__(self):
        for value in (self.key, self.secret, self.passphrase):
            if not isinstance(value, str) or not re.fullmatch(r"[!-~]{1,256}", value):
                raise ValueError("Invalid demo credential configuration")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"sk[0-9a-f]{28}", value):
        raise ValueError("Invalid strategy client ID")
    return value


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,32}(?:\.[0-9]{1,32})?", value):
        raise ValueError("Invalid demo order amount")
    number = Decimal(value)
    if number <= 0:
        raise ValueError("Invalid demo order amount")
    return number


def entry_body(body):
    required = {"instId", "tdMode", "posSide", "side", "ordType", "sz", "clOrdId", "attachAlgoOrds"}
    if not isinstance(body, dict) or set(body) != required:
        raise ValueError("Unsupported demo entry fields")
    if (body["instId"] not in SYMBOLS or body["side"] not in {"buy", "sell"}
            or body["tdMode"] != "isolated" or body["posSide"] != "net"
            or body["ordType"] != "market"):
        raise ValueError("Unsupported demo entry mode")
    identifier(body["clOrdId"])
    amount(body["sz"])
    attached = body["attachAlgoOrds"]
    if not isinstance(attached, list) or len(attached) != 1 or not isinstance(attached[0], dict):
        raise ValueError("One attached TP/SL bracket required")
    bracket = attached[0]
    if set(bracket) != {"attachAlgoClOrdId", "tpTriggerPx", "tpOrdPx", "tpTriggerPxType",
                         "slTriggerPx", "slOrdPx", "slTriggerPxType"}:
        raise ValueError("Unsupported demo protective fields")
    if (bracket["attachAlgoClOrdId"] != "sa" + body["clOrdId"][2:]
            or bracket["tpOrdPx"] != "-1" or bracket["slOrdPx"] != "-1"
            or bracket["tpTriggerPxType"] != "last" or bracket["slTriggerPxType"] != "last"):
        raise ValueError("Invalid native market protection")
    stop, target = amount(bracket["slTriggerPx"]), amount(bracket["tpTriggerPx"])
    if (body["side"] == "buy" and stop >= target) or (body["side"] == "sell" and stop <= target):
        raise ValueError("Invalid protective direction")
    # Freeze nested structures and reject non-JSON additions before any request.
    return json.loads(json.dumps(body, allow_nan=False))


class OKXDemoTransport:
    environment = "demo"

    def __init__(self, credentials=None, *, enable_demo_requests=False, transport=None, clock=None):
        self.credentials = credentials
        self.enabled = enable_demo_requests is True
        self.rehearsal_only = isinstance(transport, httpx.MockTransport)
        self._transport = transport
        self._clock = clock or (lambda: int(time.time() * 1000))

    def _require_enabled(self):
        if not self.enabled:
            raise DemoTransportError("Demo requests are disabled by default")
        if not isinstance(self.credentials, DemoCredentials):
            raise DemoTransportError("Demo credentials are not configured")

    async def place_entry(self, body, headers, *, deadline_ms=None):
        self._require_enabled()
        if headers != DEMO_HEADERS:
            raise ValueError("Fixed demo header required")
        return await self._send("POST", "/api/v5/trade/order", entry_body(body), deadline_ms=deadline_ms)

    async def lookup_entry(self, symbol, client_id):
        self._require_enabled()
        if symbol not in SYMBOLS:
            raise ValueError("Instrument not allowlisted")
        query = urlencode({"instId": symbol, "clOrdId": identifier(client_id)})
        return await self._send("GET", "/api/v5/trade/order?" + query)

    async def cancel_entry(self, body, headers):
        self._require_enabled()
        if headers != DEMO_HEADERS or not isinstance(body, dict) or set(body) != {"instId", "clOrdId"}:
            raise ValueError("Fixed demo cancel fields required")
        if body["instId"] not in SYMBOLS:
            raise ValueError("Instrument not allowlisted")
        identifier(body["clOrdId"])
        return await self._send("POST", "/api/v5/trade/cancel-order", dict(body))

    async def _send(self, method, path, body=None, *, deadline_ms=None):
        self._require_enabled()
        # No caller-supplied URL, auth headers, redirect, proxy config or retry policy.
        if not ((method == "GET" and path.startswith("/api/v5/trade/order?"))
                or (method == "POST" and path in {"/api/v5/trade/order", "/api/v5/trade/cancel-order"})):
            raise ValueError("Demo endpoint is not permitted")
        now = self._clock()
        if type(now) is not int or not 0 <= now <= 253402300799000:
            raise ValueError("Invalid UTC clock")
        stamp = datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        encoded = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        signature = base64.b64encode(hmac.new(self.credentials.secret.encode(),
            (stamp + method + path + encoded).encode(), hashlib.sha256).digest()).decode()
        headers = {"Content-Type": "application/json", "x-simulated-trading": "1",
            "OK-ACCESS-KEY": self.credentials.key, "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
            "OK-ACCESS-TIMESTAMP": stamp, "OK-ACCESS-SIGN": signature}
        if method == "POST" and path == "/api/v5/trade/order":
            deadline = now + 1500 if deadline_ms is None else deadline_ms
            if type(deadline) is not int or not now < deadline <= now + 20000:
                raise ValueError("Invalid or expired entry deadline")
            headers["expTime"] = str(min(deadline, now + 1500))
        try:
            transport = self._transport if self._transport is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
            async with asyncio.timeout(TIMEOUT_SECONDS):
                async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS,
                        follow_redirects=False, trust_env=False) as client:
                    async with client.stream(method, ORIGIN + path, headers=headers,
                            content=encoded.encode() if body is not None else None) as response:
                        if response.status_code != 200:
                            raise ValueError("Unexpected response status")
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > MAX_RESPONSE_BYTES:
                                raise ValueError("Response exceeds bound")
            parsed = json.loads(payload)
            if (not isinstance(parsed, dict) or not isinstance(parsed.get("code"), str)
                    or not re.fullmatch(r"[0-9]{1,8}", parsed["code"])
                    or not isinstance(parsed.get("data"), list) or len(parsed["data"]) > 1):
                raise ValueError("Malformed response")
            fields = ORDER_FIELDS if method == "GET" else ACK_FIELDS
            rows = []
            for row in parsed["data"]:
                if not isinstance(row, dict):
                    raise ValueError("Malformed response row")
                safe = {}
                for key in fields & row.keys():
                    if not isinstance(row[key], str) or len(row[key]) > 128:
                        raise ValueError("Malformed response value")
                    safe[key] = row[key]
                rows.append(safe)
            return {"code": parsed["code"], "data": rows}
        except Exception:
            # Do not expose httpx's request object, headers, raw body or upstream msg.
            raise DemoTransportError("Demo request failed; reconcile before any retry") from None
