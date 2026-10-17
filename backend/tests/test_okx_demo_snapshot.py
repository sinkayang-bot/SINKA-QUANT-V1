"""Complete intercepted GET snapshots; no credentials or exchange activity."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import httpx
from unittest.mock import patch

from okx_demo_authority import DemoEntryAuthority
from okx_demo_executor import DemoExecutionJournal
from okx_demo_reads import ALGO_TYPES, read_route
from okx_demo_snapshot import DemoSnapshotReader, DemoSnapshotError
from okx_demo_transport import OKXDemoTransport, DemoCredentials, DemoTransportError
from test_okx_strategy_bridge import args, READY

UID = "123456"
NOW = READY+1000


class Fixture:
    def __init__(self):
        self.calls = []
        self.counts = {}
        self.config = dict(uid=UID, posMode="net_mode", acctLv="2", perm="read_only,trade", extra="private")
        self.balance = dict(details=[dict(ccy="USDT", eq="10000", availEq="9500", availBal="9700", extra="private")])
        self.leverage = [dict(instId="BTC-USDT-SWAP", posSide="net", mgnMode="isolated", lever="3")]
        self.instrument = args()["instrument"] | dict(upcChg=[])
        self.positions = []
        self.orders = []
        self.algos = {kind:[] for kind in ALGO_TYPES}
        self.fills = []
        self.mutate = None
        self.response_override = None
        self.now = NOW

    def handler(self, request):
        self.calls.append(request)
        if request.method != "GET": raise AssertionError("Snapshot may NEVER write")
        if request.headers["x-simulated-trading"] != "1": raise AssertionError("Demo header missing")
        path = request.url.path
        q = dict(request.url.params)
        self.counts[path] = self.counts.get(path,0)+1
        if self.mutate: self.mutate(self, path, self.counts[path])
        if self.response_override:
            changed = self.response_override(request)
            if changed is not None: return changed
        if path.endswith("/config"): rows = [self.config]
        elif path.endswith("/balance"):
            if q != {"ccy":"USDT"}: raise AssertionError("Currency filter must be USDT")
            rows = [self.balance]
        elif path.endswith("/leverage-info"): rows = self.leverage
        elif path.endswith("/instruments"): rows = [self.instrument]
        elif path.endswith("/positions"):
            if q: raise AssertionError("Positions must include ALL account exposure")
            rows = self.positions
        elif path.endswith("/orders-pending"):
            if "instType" in q or "instId" in q: raise AssertionError("No hidden normal-order filter")
            rows = self.orders
        elif path.endswith("/orders-algo-pending"):
            if "instType" in q or "instId" in q: raise AssertionError("No hidden algo-order filter")
            rows = self.algos[q["ordType"]]
        elif path.endswith("/fills"):
            if q.get("instType") != "SWAP": raise AssertionError("Wrong fill scope")
            rows = self.fills
        else: raise AssertionError("Unexpected endpoint")
        if path.endswith(("/orders-pending", "/orders-algo-pending", "/fills")):
            field = "ordId" if path.endswith("/orders-pending") else "algoId" if path.endswith("/orders-algo-pending") else "billId"
            if "after" in q: rows = [r for r in rows if int(r[field]) < int(q["after"])]
            rows = rows[:100]
        return httpx.Response(200, json={"code":"0", "data":deepcopy(rows), "msg":"private"})

    def reader(self, **updates):
        transport = OKXDemoTransport(DemoCredentials("synthetic-key", "synthetic-secret", "synthetic-pass"),
            enable_demo_requests=True, transport=httpx.MockTransport(self.handler), clock=lambda:self.now)
        values = dict(account_scope="synthetic-v1", expected_uid=UID, symbol="BTC-USDT-SWAP", clock=lambda:self.now)
        values.update(updates)
        return DemoSnapshotReader(transport, **values)


def pending(i):
    return dict(ordId=str(i), clOrdId="", instId="ETH-USDT", instType="SPOT", state="live", sz="1",
                accFillSz="0", side="buy", posSide="net", tdMode="cash", ordType="limit")


def fill(i, instrument="BTC-USDT-SWAP", trade="1"):
    return dict(billId=str(i), tradeId=trade, ordId="123", instId=instrument, side="buy", posSide="net",
                fillSz="1", fillPx="100", fillTime=str(NOW), fee="-0.05", feeCcy="USDT", fillPnl="0")


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_stable_double_read_uses_currency_equity_and_oldest_time_never_auto_audits(self):
        f = Fixture(); reader = f.reader()
        snapshot = await reader.collect()
        self.assertEqual(snapshot["equity_usdt"], "10000")
        self.assertEqual(snapshot["available_usdt"], "9500")
        self.assertEqual(snapshot["received_at_ms"], NOW)
        self.assertTrue(snapshot["order_permission_verified"])
        self.assertTrue(snapshot["complete"])
        self.assertFalse(snapshot["fill_audit_ready"])
        self.assertFalse(snapshot["fill_history_complete"])
        self.assertNotIn("uid", snapshot)
        self.assertNotIn("private", json.dumps(snapshot))
        self.assertEqual(f.counts["/api/v5/account/config"], 2)
        self.assertEqual(f.counts["/api/v5/trade/orders-algo-pending"], 10)
        self.assertTrue(all(r.method=="GET" for r in f.calls))

    async def test_wrong_account_or_unsupported_mode_stops_before_any_amount_reads(self):
        for change in (dict(uid="9999"), dict(posMode="long_short_mode"), dict(acctLv="4"), dict(perm="read_only,withdraw")):
            f = Fixture(); f.config.update(change)
            with self.subTest(change=change), self.assertRaises(DemoSnapshotError): await f.reader().collect()
            self.assertEqual(len(f.calls), 1)

    async def test_read_only_key_is_diagnostic_and_cannot_authorize_entry(self):
        f = Fixture(); f.config["perm"] = "read_only"
        snapshot = await f.reader().collect()
        self.assertFalse(snapshot["order_permission_verified"])
        with tempfile.TemporaryDirectory() as temp:
            journal = DemoExecutionJournal(Path(temp)/"ledger.sqlite")
            try:
                authority = DemoEntryAuthority(journal, account_scope="synthetic-v1", worker_id="w", clock=lambda:NOW)
                authority.acquire(); authority.set_kill_switch(False)
                with self.assertRaises(ValueError): authority.accept_snapshot(snapshot)
                self.assertFalse(authority.recovery_allowed())
            finally: journal.close()

    async def test_fresh_account_reads_alone_do_not_satisfy_fill_audit(self):
        f = Fixture(); snapshot = await f.reader().collect()
        with tempfile.TemporaryDirectory() as temp:
            journal = DemoExecutionJournal(Path(temp)/"ledger.sqlite")
            try:
                authority = DemoEntryAuthority(journal, account_scope="synthetic-v1", worker_id="w", clock=lambda:NOW)
                authority.acquire(); authority.set_kill_switch(False)
                with self.assertRaises(ValueError): authority.accept_snapshot(snapshot)
                self.assertIsNone(authority.snapshot)
            finally: journal.close()

    async def test_all_pending_normal_orders_paginate_including_foreign_spot_orders(self):
        f = Fixture(); f.orders = [pending(i) for i in range(250,0,-1)]
        snapshot = await f.reader().collect()
        self.assertEqual(len(snapshot["pending_orders"]), 250)
        self.assertEqual(f.counts["/api/v5/trade/orders-pending"], 6)
        self.assertTrue(any(r.url.params.get("after")=="151" for r in f.calls))

    async def test_exact_full_page_requires_following_terminal_page(self):
        f = Fixture(); f.orders = [pending(i) for i in range(100,0,-1)]
        snapshot = await f.reader().collect()
        self.assertEqual(len(snapshot["pending_orders"]), 100)
        self.assertEqual(f.counts["/api/v5/trade/orders-pending"], 4)

    async def test_duplicate_or_repeated_cursor_page_fails_closed(self):
        f = Fixture(); page = [pending(i) for i in range(100,0,-1)]
        f.response_override = lambda r:httpx.Response(200,json={"code":"0","data":page}) if r.url.path.endswith("/orders-pending") else None
        with self.assertRaises(DemoSnapshotError): await f.reader().collect()

    async def test_scan_limit_is_not_silently_truncated(self):
        f = Fixture(); f.orders = [pending(i) for i in range(501,0,-1)]
        with self.assertRaises(DemoSnapshotError): await f.reader().collect()

    async def test_each_supported_algo_type_and_foreign_exposure_is_retained(self):
        f = Fixture()
        for i, kind in enumerate(ALGO_TYPES):
            f.algos[kind] = [dict(algoId=str(i+1), instId="ETH-USDT", instType="SPOT", state="live",
                ordType=kind, sz="1", side="sell", posSide="net", tdMode="cash", reduceOnly=False)]
        snapshot = await f.reader().collect()
        self.assertEqual(len(snapshot["pending_algos"]), len(ALGO_TYPES))
        self.assertEqual({r.url.params.get("ordType") for r in f.calls if "algo-pending" in r.url.path}, set(ALGO_TYPES))

    async def test_same_algo_id_cannot_appear_in_different_type_scans(self):
        f = Fixture()
        for kind in ALGO_TYPES[:2]: f.algos[kind] = [dict(algoId="1", instId="BTC-USDT-SWAP", ordType=kind, state="live")]
        with self.assertRaises(DemoSnapshotError): await f.reader().collect()

    async def test_changed_account_groups_reject_whole_snapshot_and_clear_cache(self):
        for endpoint in ("/balance", "/positions", "/orders-pending", "/orders-algo-pending", "/leverage-info", "/instruments", "/config", "/fills"):
            f = Fixture(); reader = f.reader()
            await reader.collect()
            f.counts.clear()
            def mutate(f, path, count):
                threshold = 6 if endpoint == "/orders-algo-pending" else 2
                if not path.endswith(endpoint) or count < threshold: return
                if endpoint == "/balance": f.balance["details"][0]["eq"]="9999"
                if endpoint == "/positions": f.positions=[dict(instId="BTC-USDT-SWAP", posId="1", mgnMode="isolated", posSide="net", pos="1")]
                if endpoint == "/orders-pending": f.orders=[pending(123)]
                if endpoint == "/orders-algo-pending": f.algos["oco"]=[dict(algoId="1", instId="BTC-USDT-SWAP", ordType="oco", state="live")]
                if endpoint == "/leverage-info": f.leverage[0]["lever"]="2"
                if endpoint == "/instruments": f.instrument["tickSz"]="0.01"
                if endpoint == "/config": f.config["perm"]="read_only"
                if endpoint == "/fills": f.fills=[fill(100)]
            f.mutate=mutate
            with self.subTest(endpoint=endpoint), self.assertRaises(DemoSnapshotError): await reader.collect()
            self.assertIsNone(reader.last_snapshot)

    async def test_ambiguous_currency_or_leverage_and_upcoming_spec_change_fail(self):
        for change in ("missing_equity", "empty_available", "duplicate_usdt", "missing_leverage", "wrong_leverage", "upcoming"):
            f=Fixture()
            if change=="missing_equity": f.balance["details"][0].pop("eq")
            if change=="empty_available": f.balance["details"][0]["availEq"]=""
            if change=="duplicate_usdt": f.balance["details"].append(deepcopy(f.balance["details"][0]))
            if change=="missing_leverage": f.leverage=[]
            if change=="wrong_leverage": f.leverage[0]["lever"]="10"
            if change=="upcoming": f.instrument["upcChg"]=[dict(param="tickSz",newValue="0.01",effTime=str(NOW+1))]
            with self.subTest(change=change), self.assertRaises(DemoSnapshotError): await f.reader().collect()

    async def test_fill_window_keeps_fees_without_inventing_complete_history(self):
        f=Fixture(); f.fills=[fill(100,trade="1"), fill(99,instrument="ETH-USDT-SWAP",trade="1")]
        snapshot=await f.reader().collect()
        self.assertEqual(len(snapshot["recent_fills"]),2)  # Trade IDs are instrument-scoped.
        self.assertEqual(snapshot["recent_fills"][0]["feeCcy"],"USDT")
        self.assertFalse(snapshot["fill_audit_ready"])
        f.fills=[fill(100),fill(99)]
        with self.assertRaises(DemoSnapshotError): await f.reader().collect()

    async def test_finite_oldest_receive_time_and_no_clock_rollback(self):
        f=Fixture()
        def mutate(f,path,count):
            if path.endswith("/config") and count==2: f.now=NOW+5001
        f.mutate=mutate
        with self.assertRaises(DemoSnapshotError): await f.reader().collect()
        f=Fixture(); f.mutate=lambda f,p,c:setattr(f,"now",NOW-1) if p.endswith("/config") and c==2 else None
        with self.assertRaises(DemoSnapshotError): await f.reader().collect()

    async def test_full_read_pages_have_separate_bound_and_unknown_fields_are_dropped(self):
        f=Fixture(); f.orders=[pending(i) | {"extra":"x"*1000} for i in range(100,0,-1)]
        snapshot=await f.reader().collect()
        self.assertEqual(len(snapshot["pending_orders"]),100)
        self.assertTrue(all("extra" not in row for row in snapshot["pending_orders"]))
        f=Fixture(); f.response_override=lambda r:httpx.Response(200,content=b"x"*1048577)
        with self.assertRaises(DemoSnapshotError): await f.reader().collect()

    async def test_future_fill_or_nonfinite_fee_never_becomes_audited_history(self):
        for change in (dict(fillTime=str(NOW+1)),dict(fee="NaN"),dict(fillSz="0"),dict(feeCcy="BTC")):
            f=Fixture(); f.fills=[fill(100) | change]
            with self.subTest(change=change), self.assertRaises(DemoSnapshotError): await f.reader().collect()

    async def test_read_error_is_sanitized_and_no_posts_are_made(self):
        f=Fixture(); f.response_override=lambda r:httpx.Response(403,text="sensitive credential context")
        with self.assertRaises(DemoSnapshotError) as err: await f.reader().collect()
        self.assertNotIn("sensitive",str(err.exception))
        self.assertTrue(err.exception.__suppress_context__)
        self.assertTrue(all(r.method=="GET" for r in f.calls))

    async def test_invalid_read_operations_never_reach_http(self):
        f=Fixture(); transport=f.reader().transport
        for kind, params in (("set_leverage",{}),("config",{"after":"1"}),("balance",{"symbol":"BTC-USDT-SWAP"}),
            ("leverage",{}),("instrument",{"symbol":"UNKNOWN"}),("algos",{"algo_type":"other"}),
            ("orders",{"after":"../escape"})):
            with self.subTest(kind=kind), self.assertRaises(ValueError): await transport.account_read(kind,**params)
        self.assertEqual(f.calls,[])
        disabled=OKXDemoTransport()
        with self.assertRaises(DemoTransportError): await disabled.account_read("config")


if __name__ == "__main__": unittest.main()
