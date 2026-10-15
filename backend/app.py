"""SINKA QUANT paper-only server. No private OKX endpoints or real order execution."""
import asyncio
import hmac
import math
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DB = os.getenv("DB_PATH", "/data/paper.db")
TOKEN = os.getenv("PAPER_API_TOKEN", "")
SYMBOLS = {"BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP"}
FEE = 0.0005
SLIP = 0.0002
RISK = 0.005
LEVERAGE = 3.0
INITIAL = 10000.0
lock = asyncio.Lock()


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), cash REAL NOT NULL)")
        db.execute("INSERT OR IGNORE INTO account VALUES (1, ?)", (INITIAL,))
        db.execute("""CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, side INTEGER NOT NULL,
            qty REAL NOT NULL, entry REAL NOT NULL, stop REAL NOT NULL, opened INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side INTEGER, qty REAL,
            entry REAL, exit REAL, pnl REAL, reason TEXT, closed INTEGER)""")


async def price(symbol: str) -> float:
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get("https://www.okx.com/api/v5/market/ticker", params={"instId": symbol})
        response.raise_for_status()
        data = response.json()
    if data.get("code") != "0" or not data.get("data"):
        raise HTTPException(503, "OKX market price unavailable")
    item = data["data"][0]
    px = float(item["last"])
    ts = int(item["ts"])
    if not math.isfinite(px) or px <= 0 or abs(time.time() * 1000 - ts) > 20000:
        raise HTTPException(503, "Stale or invalid market price")
    return px


async def auth(authorization: str | None = Header(default=None)):
    if not TOKEN or not authorization or not hmac.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "Unauthorized")


class OpenRequest(BaseModel):
    symbol: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP"]
    side: Literal[-1, 1]
    stop: float = Field(gt=0, allow_inf_nan=False)


def close_position(db, pos, px: float, reason: str):
    exit_px = px * (1 - pos["side"] * SLIP)
    gross = pos["qty"] * pos["side"] * (exit_px - pos["entry"])
    closing_fee = pos["qty"] * exit_px * FEE
    net_after_entry_fee = gross - closing_fee - pos["qty"] * pos["entry"] * FEE
    db.execute("UPDATE account SET cash=cash+? WHERE id=1", (gross - closing_fee,))
    db.execute("INSERT INTO fills (symbol,side,qty,entry,exit,pnl,reason,closed) VALUES (?,?,?,?,?,?,?,?)",
               (pos["symbol"], pos["side"], pos["qty"], pos["entry"], exit_px, net_after_entry_fee, reason, int(time.time())))
    db.execute("DELETE FROM positions WHERE id=?", (pos["id"],))


async def monitor():
    while True:
        await asyncio.sleep(5)
        try:
            async with lock:
                with connect() as db:
                    pos = db.execute("SELECT * FROM positions LIMIT 1").fetchone()
                if not pos:
                    continue
                px = await price(pos["symbol"])
                if (pos["side"] == 1 and px <= pos["stop"]) or (pos["side"] == -1 and px >= pos["stop"]):
                    with connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        current = db.execute("SELECT * FROM positions WHERE id=?", (pos["id"],)).fetchone()
                        if current:
                            close_position(db, current, px, "stop (polled)")
        except Exception as exc:
            print("monitor warning:", type(exc).__name__, str(exc), flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(monitor())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="SINKA QUANT Paper Engine", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "paper_only", "live_orders": False}


@app.get("/api/state", dependencies=[Depends(auth)])
async def state():
    with connect() as db:
        cash = db.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
        pos = db.execute("SELECT * FROM positions LIMIT 1").fetchone()
        fills = db.execute("SELECT * FROM fills ORDER BY id DESC LIMIT 100").fetchall()
    current_price = None
    unrealized = None
    if pos:
        try:
            current_price = await price(pos["symbol"])
            unrealized = pos["qty"] * pos["side"] * (current_price - pos["entry"]) - pos["qty"] * current_price * FEE
        except Exception:
            pass
    return {"cash": cash, "equity": cash + unrealized if unrealized is not None else (cash if not pos else None),
            "unrealized": unrealized, "current_price": current_price,
            "position": dict(pos) if pos else None, "fills": [dict(x) for x in fills]}


@app.get("/api/performance", dependencies=[Depends(auth)])
async def performance():
    """Realized paper metrics only; independent of browser historical backtests."""
    with connect() as db:
        cash = db.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
        fills = db.execute("SELECT pnl FROM fills ORDER BY id").fetchall()
        position = db.execute("SELECT 1 FROM positions LIMIT 1").fetchone()
    pnls = [float(row["pnl"]) for row in fills]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    equity = INITIAL
    peak = INITIAL
    max_dd = 0.0
    for result in pnls:
        equity += result
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak) if peak > 0 else max_dd
    return {
        "mode": "paper_only",
        "initial_balance": INITIAL,
        "cash": cash,
        "realized_pnl": cash - INITIAL,
        "closed_trades": len(pnls),
        "win_rate_pct": 100 * len(wins) / len(pnls) if pnls else None,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "max_closed_equity_drawdown_pct": 100 * max_dd,
        "has_open_position": bool(position),
        "note": "Closed-trade metrics only; drawdown excludes intratrade equity and open positions.",
    }


@app.post("/api/open", dependencies=[Depends(auth)])
async def open_paper(req: OpenRequest):
    async with lock:
        px = await price(req.symbol)
        entry = px * (1 + req.side * SLIP)
        dist = req.side * (entry - req.stop)
        if not math.isfinite(dist) or dist <= 0:
            raise HTTPException(400, "Stop must be on the loss side")
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM positions LIMIT 1").fetchone():
                raise HTTPException(409, "Only one position supported")
            cash = db.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
            qty = min(cash * RISK / (dist + entry * FEE + req.stop * FEE), cash * LEVERAGE / entry)
            fee = qty * entry * FEE
            if qty <= 0 or fee >= cash:
                raise HTTPException(400, "Insufficient paper balance")
            db.execute("UPDATE account SET cash=cash-? WHERE id=1", (fee,))
            db.execute("INSERT INTO positions(symbol,side,qty,entry,stop,opened) VALUES (?,?,?,?,?,?)",
                       (req.symbol, req.side, qty, entry, req.stop, int(time.time())))
    return {"status": "paper_opened", "symbol": req.symbol, "entry": entry, "qty": qty}


@app.post("/api/close", dependencies=[Depends(auth)])
async def close_paper():
    async with lock:
        with connect() as db:
            pos = db.execute("SELECT * FROM positions LIMIT 1").fetchone()
        if not pos:
            raise HTTPException(404, "No open position")
        px = await price(pos["symbol"])
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM positions WHERE id=?", (pos["id"],)).fetchone()
            if not current:
                raise HTTPException(409, "Position already closed")
            close_position(db, current, px, "manual")
    return {"status": "paper_closed"}
