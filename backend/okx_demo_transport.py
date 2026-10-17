"""Default-off OKX demo-only request transport. Not wired to any runtime.

No credential discovery, live mode, custom host, generic request API or retries.
Tests inject httpx.MockTransport; they never contact an exchange.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
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
from okx_demo_reads import SCHEMAS, read_route, safe_row, bill_route

ORIGIN = "https://www.okx.com"
MAX_RESPONSE_BYTES = 65536
MAX_READ_RESPONSE_BYTES = 1048576
TIMEOUT_SECONDS = 5.0
DEMO_HEADERS = {"x-simulated-trading": "1"}
ACK_FIELDS = frozenset({"sCode", "ordId", "clOrdId"})
ORDER_FIELDS = frozenset({"ordId", "clOrdId", "instId", "state", "sz", "accFillSz",
    "side", "posSide", "tdMode", "ordType", "avgPx", "fee", "feeCcy", "fillPx", "algoId", "algoClOrdId", "reduceOnly"})


class DemoTransportError(RuntimeError):
    def __init__(self,message,*,http_status=None):
        super().__init__(message)
        self.http_status=(http_status if type(http_status) is int and 100<=http_status<=599 else None)


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
        self._read_client = None
        self._read_slots = None
        self._read_active = False

    def _require_enabled(self):
        if not self.enabled:
            raise DemoTransportError("Demo requests are disabled by default")
        if not isinstance(self.credentials, DemoCredentials):
            raise DemoTransportError("Demo credentials are not configured")

    @asynccontextmanager
    async def read_session(self):
        """One bounded snapshot pool. At most two concurrent GETs, no writes."""
        self._require_enabled()
        if self._read_active:
            raise DemoTransportError('A read session is already active')
        self._read_active=True
        try:
            limits=httpx.Limits(max_connections=2,max_keepalive_connections=2)
            transport=self._transport if self._transport is not None else httpx.AsyncHTTPTransport(
                retries=0,trust_env=False,limits=limits)
            async with httpx.AsyncClient(transport=transport,timeout=TIMEOUT_SECONDS,
                    limits=limits,follow_redirects=False,trust_env=False) as client:
                self._read_client=client
                self._read_slots=asyncio.Semaphore(2)
                yield
        finally:
            self._read_client=None
            self._read_slots=None
            self._read_active=False

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

    async def lookup_algo(self, attached_client_id):
        self._require_enabled()
        if not isinstance(attached_client_id,str) or not re.fullmatch(r"sa[0-9a-f]{28}",attached_client_id):
            raise ValueError("Tracked attached client identity required")
        return await self._send("GET", "/api/v5/trade/order-algo?"+urlencode({"algoClOrdId":attached_client_id}))

    async def lookup_child(self, symbol, order_id):
        self._require_enabled()
        if (symbol not in SYMBOLS or not isinstance(order_id,str)
                or not re.fullmatch(r"[1-9][0-9]{0,39}",order_id)):
            raise ValueError("Tracked child identity required")
        return await self._send("GET", "/api/v5/trade/order?"+urlencode({"instId":symbol,"ordId":order_id}))

    async def cancel_entry(self, body, headers):
        self._require_enabled()
        if headers != DEMO_HEADERS or not isinstance(body, dict) or set(body) != {"instId", "clOrdId"}:
            raise ValueError("Fixed demo cancel fields required")
        if body["instId"] not in SYMBOLS:
            raise ValueError("Instrument not allowlisted")
        identifier(body["clOrdId"])
        return await self._send("POST", "/api/v5/trade/cancel-order", dict(body))

    async def account_read(self, kind, *, symbol=None, algo_type=None, after=None):
        """GET-only fixed read operations; no arbitrary URL, filter or endpoint."""
        self._require_enabled()
        path = read_route(kind, symbol=symbol, algo_type=algo_type, after=after)
        return await self._send("GET", path)

    async def read_bills(self, begin_ms, end_ms, *, after=None):
        """GET-only complete USDT interval; fixed endpoint and filters."""
        self._require_enabled()
        return await self._send("GET", bill_route(begin_ms, end_ms, after=after))

    async def _send(self, method, path, body=None, *, deadline_ms=None):
        if self._read_client is not None:
            if method!='GET' or body is not None or deadline_ms is not None:
                raise DemoTransportError('Read sessions permit GET only')
            client,slots=self._read_client,self._read_slots
            async with slots:
                if self._read_client is not client:
                    raise DemoTransportError('Read session has ended')
                return await self._send_request(method,path,body,deadline_ms=deadline_ms,client=client)
        return await self._send_request(method,path,body,deadline_ms=deadline_ms)

    async def _send_request(self, method, path, body=None, *, deadline_ms=None,client=None):
        self._require_enabled()
        # No caller-supplied URL, auth headers, redirect, proxy config or retry policy.
        root = path.partition("?")[0]
        if not ((method == "GET" and (path.startswith("/api/v5/trade/order?") or root in SCHEMAS))
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
        async def receive(active_client):
            # Do not reuse API cookies between authenticated requests.
            active_client.cookies.clear()
            async with active_client.stream(method, ORIGIN + path, headers=headers,
                    content=encoded.encode() if body is not None else None) as response:
                if response.status_code != 200:
                    raise DemoTransportError("Demo request failed; reconcile before any retry",http_status=response.status_code)
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > (MAX_READ_RESPONSE_BYTES if method == "GET" and root in SCHEMAS else MAX_RESPONSE_BYTES):
                        raise ValueError("Response exceeds bound")
            return payload
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                if client is None:
                    transport=self._transport if self._transport is not None else httpx.AsyncHTTPTransport(retries=0,trust_env=False)
                    async with httpx.AsyncClient(transport=transport,timeout=TIMEOUT_SECONDS,
                            follow_redirects=False,trust_env=False) as private_client:
                        payload=await receive(private_client)
                else:
                    payload=await receive(client)
            parsed = json.loads(payload)
            max_rows, fields = SCHEMAS[root] if method == "GET" and root in SCHEMAS else (1, ORDER_FIELDS if method == "GET" else ACK_FIELDS)
            if (not isinstance(parsed, dict) or not isinstance(parsed.get("code"), str)
                    or not re.fullmatch(r"[0-9]{1,8}", parsed["code"])
                    or not isinstance(parsed.get("data"), list) or len(parsed["data"]) > max_rows):
                raise ValueError("Malformed response")
            rows = [safe_row(row, fields) for row in parsed["data"]]
            return {"code": parsed["code"], "data": rows}
        except DemoTransportError:
            raise
        except Exception:
            # Do not expose httpx's request object, headers, raw body or upstream msg.
            raise DemoTransportError("Demo request failed; reconcile before any retry") from None


class OKXDemoReadTransport(OKXDemoTransport):
    """Structural GET-only transport for reconciliation runtimes."""
    async def _send(self,method,path,body=None,*,deadline_ms=None):
        if method!='GET' or body is not None or deadline_ms is not None:
            raise DemoTransportError('Reconciliation transport permits GET only')
        return await super()._send(method,path)

    async def _send_request(self,method,path,body=None,*,deadline_ms=None,client=None):
        if method!='GET' or body is not None or deadline_ms is not None:
            raise DemoTransportError('Reconciliation transport permits GET only')
        return await super()._send_request(method,path,body,deadline_ms=deadline_ms,client=client)

    async def place_entry(self,*args,**kwargs):
        raise DemoTransportError('Reconciliation transport cannot place orders')

    async def cancel_entry(self,*args,**kwargs):
        raise DemoTransportError('Reconciliation transport cannot cancel orders')
