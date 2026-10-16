"""Isolated Firebase-owner-only OKX demo status API candidate; not deployed.

Only GET diagnostics are exposed. No paper ledger, order routes, scheduler,
background worker, secret payload endpoint or persistent account data storage.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from firebase_auth import verify_owner_token
from okx_readonly import OKXReadOnly
from okx_smoke import inspect_demo

CHECK_TIMEOUT_SECONDS = 30
FRESH_SECONDS = 30
STAGES = frozenset({"account_config", "balances", "positions", "instruments", "pending_orders"})


def create_app():
    app = FastAPI(title="SINKA QUANT Demo Read-only Status API",
                  docs_url=None, redoc_url=None, openapi_url=None)
    check_lock = asyncio.Lock()
    app.add_middleware(CORSMiddleware,
                       allow_origins=["https://sinkayang-bot.github.io"],
                       allow_methods=["GET"], allow_headers=["Authorization"],
                       allow_credentials=False)

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        # Process liveness only. Never claims the exchange was checked.
        return {"status": "ok", "mode": "demo_readonly", "trading_enabled": False,
                "okx_connection_verified": False}

    @app.get("/api/okx/status")
    async def status(owner: str = Depends(verify_owner_token)):
        if (os.getenv("SINKA_OKX_READONLY_API") != "true"
                or os.getenv("SINKA_OKX_DEMO") != "true"):
            raise HTTPException(503, "Demo read-only status API is disabled")
        if check_lock.locked():
            raise HTTPException(429, "A read-only check is already running", headers={"Retry-After": "5"})
        async with check_lock:
            try:
                reader = OKXReadOnly(demo=True)
            except ValueError:
                raise HTTPException(503, "Read-only credentials are not configured") from None
            try:
                result = await asyncio.wait_for(inspect_demo(reader), timeout=CHECK_TIMEOUT_SECONDS)
            except TimeoutError:
                return JSONResponse(status_code=504, content={
                    "mode": "demo_readonly", "okx_connection_verified": False,
                    "reason": "readonly_check_timeout", "trading_enabled": False})
            except Exception:
                raise HTTPException(503, "Read-only check unavailable") from None
        valid_result = (isinstance(result, dict) and result.get("ok") is True
                        and result.get("mode") == "demo_readonly"
                        and result.get("trading_enabled") is False
                        and result.get("api_permissions_readonly") is True
                        and all(result.get(name + "_endpoint") == "ok" for name in STAGES))
        if not valid_result:
            stage = result.get("failed_stage") if isinstance(result, dict) else None
            return JSONResponse(status_code=503, content={
                "mode": "demo_readonly", "okx_connection_verified": False,
                "reason": "readonly_connection_or_validation_failed",
                "failed_stage": stage if isinstance(stage, str) and stage in STAGES else "unknown",
                "trading_enabled": False})
        checked = datetime.now(timezone.utc)
        # Explicit allowlist: no raw exchange response, owner UID, balances,
        # positions, API credentials or pending order count in API responses.
        return {"mode": "demo_readonly", "okx_connection_verified": True,
                "api_permissions_readonly": result.get("api_permissions_readonly") is True,
                "checks": {name: result.get(name + "_endpoint") == "ok" for name in STAGES},
                "checked_at": checked.isoformat(),
                "valid_until": (checked + timedelta(seconds=FRESH_SECONDS)).isoformat(),
                "trading_enabled": False}

    return app


app = create_app()
