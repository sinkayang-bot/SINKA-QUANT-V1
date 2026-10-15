"""Smoke tests for isolated paper engine. Run: pip install pytest; pytest backend/tests -q"""
import importlib.util
import os
from pathlib import Path

os.environ["PAPER_API_TOKEN"] = "test-only-secret"
os.environ["DB_PATH"] = "/tmp/sinka-paper-test.db"
spec = importlib.util.spec_from_file_location("paper_app", Path(__file__).resolve().parents[1] / "app.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_schema_and_ledger(tmp_path):
    mod.DB = str(tmp_path / "paper.db")
    mod.init_db()
    with mod.connect() as db:
        assert db.execute("SELECT cash FROM account").fetchone()["cash"] == 10000
        db.execute("INSERT INTO positions(symbol,side,qty,entry,stop,opened) VALUES (?,?,?,?,?,?)",
                   ("BTC-USDT-SWAP", 1, 0.01, 100000, 99000, 1))
        pos = db.execute("SELECT * FROM positions").fetchone()
        mod.close_position(db, pos, 101000, "test")
        assert db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
        assert db.execute("SELECT cash FROM account").fetchone()[0] > 10000


def test_auth_rejects_missing_token():
    import asyncio
    from fastapi import HTTPException
    try:
        asyncio.run(mod.auth(None))
        assert False, "Expected unauthorized"
    except HTTPException as exc:
        assert exc.status_code == 401

def test_open_close_endpoints_without_real_exchange(tmp_path):
    from fastapi.testclient import TestClient
    mod.DB = str(tmp_path / "endpoint.db")

    async def fake_price(symbol):
        assert symbol == "BTC-USDT-SWAP"
        return 100000.0

    original = mod.price
    mod.price = fake_price
    try:
        with TestClient(mod.app) as client:
            headers = {"Authorization": "Bearer test-only-secret"}
            assert client.get("/api/state").status_code == 401
            initial = client.get("/api/state", headers=headers)
            assert initial.status_code == 200
            assert initial.json()["cash"] == 10000
            assert initial.json()["equity"] == 10000
            assert client.get("/api/performance").status_code == 401
            before = client.get("/api/performance", headers=headers).json()
            assert before["closed_trades"] == 0 and before["win_rate_pct"] is None
            bad = client.post("/api/open", headers=headers, json={"symbol":"BTC-USDT-SWAP","side":1,"stop":101000})
            assert bad.status_code == 400
            opened = client.post("/api/open", headers=headers, json={"symbol":"BTC-USDT-SWAP","side":1,"stop":99000})
            assert opened.status_code == 200
            assert client.post("/api/open", headers=headers, json={"symbol":"BTC-USDT-SWAP","side":1,"stop":99000}).status_code == 409
            assert client.get("/api/state", headers=headers).json()["position"] is not None
            closed = client.post("/api/close", headers=headers)
            assert closed.status_code == 200
            result = client.get("/api/state", headers=headers).json()
            assert result["position"] is None
            assert len(result["fills"]) == 1
            performance = client.get("/api/performance", headers=headers).json()
            assert performance["closed_trades"] == 1
            assert performance["win_rate_pct"] == 0
            assert performance["realized_pnl"] < 0
            assert result["cash"] < 10000  # spread/slippage and fees
            assert client.post("/api/close", headers=headers).status_code == 404
    finally:
        mod.price = original


def test_nonfinite_stop_rejected():
    from pydantic import ValidationError
    try:
        mod.OpenRequest(symbol="BTC-USDT-SWAP", side=1, stop=float("nan"))
        assert False, "Expected validation error"
    except ValidationError:
        pass
