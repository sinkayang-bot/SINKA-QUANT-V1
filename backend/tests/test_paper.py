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
