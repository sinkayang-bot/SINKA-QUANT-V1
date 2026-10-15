"""Firestore paper-only API candidate.

Run with: uvicorn firestore_app:app --host 0.0.0.0 --port 8000
Not deployed; use Firebase ID tokens from an authenticated owner.
"""
import math
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import time

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal

from firebase_auth import verify_owner_token
from firestore_ledger import PaperLedger

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if os.getenv("SINKA_ENABLE_MONITOR", "false").lower() == "true":
        if not os.environ.get("SINKA_OWNER_UID", "").strip():
            raise RuntimeError("SINKA_OWNER_UID must be configured")
        from leased_monitor import run as run_monitor
        task = asyncio.create_task(run_monitor(), name="sinka-paper-monitor")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

app = FastAPI(title="SINKA QUANT Firestore Paper API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://sinkayang-bot.github.io"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

class OpenRequest(BaseModel):
    symbol: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP"]
    side: Literal[1, -1]
    stop: float = Field(gt=0, allow_inf_nan=False)
    request_id: str = Field(min_length=8, max_length=128)

class CloseRequest(BaseModel):
    expected_id: str = Field(min_length=8, max_length=128)

async def quote(symbol: str):
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get("https://www.okx.com/api/v5/market/ticker", params={"instId": symbol})
        response.raise_for_status()
        data = response.json()
    if data.get("code") != "0" or not data.get("data"):
        raise HTTPException(503, "OKX price unavailable")
    item = data["data"][0]
    px, ts = float(item["last"]), int(item["ts"])
    if not math.isfinite(px) or px <= 0 or abs(time.time() * 1000 - ts) > 20000:
        raise HTTPException(503, "Stale OKX quote")
    return px

@app.get("/health")
def health():
    return {"status": "ok", "mode": "paper_only", "storage": "firestore", "live_orders": False}

@app.get("/api/monitor-status")
def monitor_status(owner: str = Depends(verify_owner_token)):
    ledger = PaperLedger(account_id=owner)
    record = ledger.account.collection("runtime").document("monitor").get()
    if not record.exists:
        return {"status": "never_reported", "healthy": False}
    data = record.to_dict()
    checked = data.get("last_check_at")
    age = (datetime.now(timezone.utc) - checked).total_seconds() if checked else None
    return {"status": data.get("status", "unknown"),
            "healthy": bool(age is not None and 0 <= age <= 30 and data.get("status") != "error"),
            "seconds_since_check": round(age, 1) if age is not None else None}

@app.post("/api/initialize")
def initialize(owner: str = Depends(verify_owner_token)):
    ledger = PaperLedger(account_id=owner)
    state = ledger.initialize()
    return {"status": "ready", "cash": state["cash"]}

@app.get("/api/state")
def state(owner: str = Depends(verify_owner_token)):
    return {"mode": "paper_only", "account": PaperLedger(account_id=owner).snapshot()}

@app.post("/api/open")
async def open_paper(req: OpenRequest, owner: str = Depends(verify_owner_token)):
    px = await quote(req.symbol)
    try:
        position = PaperLedger(account_id=owner).open(req.symbol, req.side, px, req.stop, req.request_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "paper_opened", "position": position}

@app.post("/api/close")
async def close_paper(req: CloseRequest, owner: str = Depends(verify_owner_token)):
    ledger = PaperLedger(account_id=owner)
    snapshot = ledger.snapshot()
    position = (snapshot or {}).get("position")
    if not position or position["id"] != req.expected_id:
        raise HTTPException(409, "Position not found or already closed")
    px = await quote(position["symbol"])
    result = ledger.close(px, "manual", req.expected_id)
    if result is None:
        raise HTTPException(409, "Position changed before close")
    return {"status": "paper_closed", "fill": result}

@app.post("/api/check-stop")
async def check_stop(owner: str = Depends(verify_owner_token)):
    """One-shot stop reconciliation; not a continuous monitor or Scheduler endpoint."""
    ledger = PaperLedger(account_id=owner)
    snapshot = ledger.snapshot()
    position = (snapshot or {}).get("position")
    if not position:
        return {"status": "no_position"}
    px = await quote(position["symbol"])
    triggered = (position["side"] == 1 and px <= position["stop"]) or (
        position["side"] == -1 and px >= position["stop"])
    if not triggered:
        return {"status": "not_triggered", "observed_price": px}
    fill = ledger.close(px, "stop (checked)", position["id"])
    return {"status": "closed" if fill else "already_closed", "fill": fill}
