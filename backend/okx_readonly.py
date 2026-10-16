"""OKX private-account read-only adapter. Never sends trading requests."""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

ALLOWED_PATHS = frozenset({
    "/api/v5/account/balance",
    "/api/v5/account/positions",
    "/api/v5/account/config",
    "/api/v5/trade/order",
    "/api/v5/trade/orders-pending",
    "/api/v5/trade/fills",
    "/api/v5/public/instruments",
})
ALLOWED_SYMBOLS = frozenset({
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
    "XRP-USDT-SWAP", "DOGE-USDT-SWAP",
})

class OKXReadOnly:
    def __init__(self, key=None, secret=None, passphrase=None, demo=True):
        self.key = key or os.environ.get("OKX_API_KEY", "")
        self.secret = secret or os.environ.get("OKX_API_SECRET", "")
        self.passphrase = passphrase or os.environ.get("OKX_API_PASSPHRASE", "")
        self.demo = demo
        if not all((self.key, self.secret, self.passphrase)):
            raise ValueError("OKX API credentials are not configured")

    def _headers(self, request_path):
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload = timestamp + "GET" + request_path
        signature = base64.b64encode(hmac.new(
            self.secret.encode(), payload.encode(), hashlib.sha256
        ).digest()).decode()
        headers = {
            "OK-ACCESS-KEY": self.key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }
        if self.demo:
            headers["x-simulated-trading"] = "1"
        return headers

    async def get(self, path, params=None):
        if path not in ALLOWED_PATHS:
            raise ValueError("Endpoint not permitted in read-only adapter")
        params = params or {}
        if path == "/api/v5/trade/fills":
            if params.get("instType") != "SWAP" or params.get("limit") != "100":
                raise ValueError("Fill scan requires SWAP and full page size")
            if any(k not in ("instType", "limit", "after") for k in params):
                raise ValueError("Unsupported fill query parameter")
            if "after" in params and not str(params["after"]).isdigit():
                raise ValueError("Invalid fill pagination cursor")
        if path == "/api/v5/trade/orders-pending":
            if params.get("instType") != "SWAP" or params.get("limit") != "100":
                raise ValueError("Pending-order scan requires SWAP and full page size")
            if any(k not in ("instType", "limit", "after") for k in params):
                raise ValueError("Unsupported pending-order query parameter")
            if "after" in params and not str(params["after"]).isdigit():
                raise ValueError("Invalid pending-order cursor")
        if path == "/api/v5/trade/order":
            if params.get("instId") not in ALLOWED_SYMBOLS or bool(params.get("ordId")) == bool(params.get("clOrdId")):
                raise ValueError("Order lookup requires allowlisted instrument and exactly one identifier")
        if path == "/api/v5/account/positions":
            inst_type = params.get("instType")
            if inst_type != "SWAP":
                raise ValueError("Only swap positions are supported")
        if path == "/api/v5/public/instruments" and params.get("instType") != "SWAP":
            raise ValueError("Only swap instruments are supported")
        query = urlencode(params)
        request_path = path + ("?" + query if query else "")
        async with httpx.AsyncClient(timeout=10, base_url="https://www.okx.com") as client:
            response = await client.get(request_path, headers=self._headers(request_path))
            response.raise_for_status()
            data = response.json()
        if data.get("code") != "0":
            raise RuntimeError("OKX rejected read-only request")
        return data.get("data", [])

    async def balances(self):
        return await self.get("/api/v5/account/balance")

    async def swap_positions(self):
        return await self.get("/api/v5/account/positions", {"instType": "SWAP"})

    async def account_config(self):
        return await self.get("/api/v5/account/config")

    async def swap_instruments(self):
        return await self.get("/api/v5/public/instruments", {"instType": "SWAP"})
