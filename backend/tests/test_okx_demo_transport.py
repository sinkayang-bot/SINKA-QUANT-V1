"""Wire-format tests with synthetic credentials and intercepted HTTP only."""
import asyncio
import base64
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from test_okx_demo_intent import plan, order
from test_okx_strategy_bridge import args, READY
from okx_demo_transport import OKXDemoTransport, DemoCredentials, DemoTransportError
from okx_demo_executor import DemoExecutor, DemoExecutionJournal

NOW = 1792252800123
CREDS = DemoCredentials("synthetic-key", "synthetic-secret", "synthetic-passphrase")
HEADERS = {"x-simulated-trading": "1"}


class DemoTransportTests(unittest.IsolatedAsyncioTestCase):
    def make(self, handler=None, **updates):
        calls = []
        def capture(request):
            calls.append(request)
            if handler: return handler(request)
            return httpx.Response(200, json={"code":"0", "msg":"private", "data":[
                {"sCode":"0", "sMsg":"private", "clOrdId":plan()["body"]["clOrdId"], "ordId":"12345"}]})
        opts = dict(enable_demo_requests=True, transport=httpx.MockTransport(capture), clock=lambda:NOW)
        opts.update(updates)
        return OKXDemoTransport(CREDS, **opts), calls

    async def test_default_off_and_missing_configuration_never_create_http_client(self):
        with patch("okx_demo_transport.httpx.AsyncClient", side_effect=AssertionError("HTTP must not start")):
            for client in (OKXDemoTransport(), OKXDemoTransport(CREDS),
                           OKXDemoTransport(enable_demo_requests=True)):
                with self.assertRaises(DemoTransportError):
                    await client.place_entry(plan()["body"], HEADERS)
                with self.assertRaises(DemoTransportError):
                    await client.lookup_entry("BTC-USDT-SWAP", plan()["body"]["clOrdId"])
                with self.assertRaises(DemoTransportError):
                    await client.cancel_entry({"instId":"BTC-USDT-SWAP", "clOrdId":plan()["body"]["clOrdId"]}, HEADERS)

    async def test_signs_exact_post_bytes_and_forces_fixed_demo_origin(self):
        client, calls = self.make()
        response = await client.place_entry(plan()["body"], HEADERS)
        request = calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "https://www.okx.com/api/v5/trade/order")
        self.assertEqual(request.headers["x-simulated-trading"], "1")
        self.assertEqual(request.headers["expTime"], str(NOW+1500))
        self.assertEqual(request.headers["OK-ACCESS-TIMESTAMP"], "2026-10-17T16:00:00.123Z")
        payload = request.headers["OK-ACCESS-TIMESTAMP"] + "POST/api/v5/trade/order" + request.content.decode()
        expected = base64.b64encode(hmac.new(CREDS.secret.encode(), payload.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(request.headers["OK-ACCESS-SIGN"], expected)
        self.assertEqual(json.loads(request.content), plan()["body"])
        self.assertEqual(response, {"code":"0", "data":[dict(sCode="0", clOrdId=plan()["body"]["clOrdId"], ordId="12345")]})

    async def test_get_signature_includes_query_and_has_no_body(self):
        p = plan()
        def handler(request): return httpx.Response(200, json={"code":"0", "data":[order(p) | {"sMsg":"private"}]})
        client, calls = self.make(handler)
        response = await client.lookup_entry("BTC-USDT-SWAP", p["body"]["clOrdId"])
        request = calls[0]
        self.assertEqual(request.content, b"")
        self.assertEqual(request.url.params["clOrdId"], p["body"]["clOrdId"])
        signed = request.headers["OK-ACCESS-TIMESTAMP"] + "GET" + request.url.raw_path.decode()
        expected = base64.b64encode(hmac.new(CREDS.secret.encode(), signed.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(request.headers["OK-ACCESS-SIGN"], expected)
        self.assertNotIn("sMsg", response["data"][0])
        self.assertNotIn("expTime", request.headers)

    async def test_cancel_only_tracked_client_id_and_fixed_post_path(self):
        client, calls = self.make()
        body = {"instId":"BTC-USDT-SWAP", "clOrdId":plan()["body"]["clOrdId"]}
        await client.cancel_entry(body, HEADERS)
        self.assertEqual(calls[0].url.path, "/api/v5/trade/cancel-order")
        self.assertEqual(json.loads(calls[0].content), body)
        self.assertNotIn("expTime", calls[0].headers)

    async def test_header_override_and_unsupported_fields_reject_before_http(self):
        client, calls = self.make()
        for headers in ({}, {"x-simulated-trading":"0"}, HEADERS | {"Authorization":"other"}):
            with self.assertRaises(ValueError): await client.place_entry(plan()["body"], headers)
        for key, value in dict(instId="BTC-USDT", tdMode="cross", posSide="long", ordType="limit",
                               side="hold", sz="NaN", clOrdId="../bad").items():
            body = deepcopy(plan()["body"]); body[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): await client.place_entry(body, HEADERS)
        with self.assertRaises(ValueError): await client.place_entry(plan()["body"] | {"url":"https://evil.invalid"}, HEADERS)
        self.assertEqual(calls, [])

    async def test_native_protection_must_remain_intact(self):
        client, calls = self.make()
        for key, value in dict(attachAlgoClOrdId="other", tpOrdPx="61200", slOrdPx="59400",
            tpTriggerPxType="index", slTriggerPxType="mark", slTriggerPx="62000").items():
            body = deepcopy(plan()["body"]); body["attachAlgoOrds"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): await client.place_entry(body, HEADERS)
        body = deepcopy(plan()["body"]); body["attachAlgoOrds"] = []
        with self.assertRaises(ValueError): await client.place_entry(body, HEADERS)
        self.assertEqual(calls, [])

    async def test_deadline_is_short_and_expired_deadline_never_sends(self):
        client, calls = self.make()
        await client.place_entry(plan()["body"], HEADERS, deadline_ms=NOW+250)
        self.assertEqual(calls[-1].headers["expTime"], str(NOW+250))
        for deadline in (NOW, NOW-1, NOW+20001, True, "tomorrow"):
            with self.assertRaises(ValueError): await client.place_entry(plan()["body"], HEADERS, deadline_ms=deadline)
        self.assertEqual(len(calls), 1)

    async def test_timeout_does_not_retry_and_hides_sensitive_errors(self):
        def timeout(request): raise httpx.ReadTimeout("synthetic-key synthetic-passphrase", request=request)
        client, calls = self.make(timeout)
        with self.assertRaises(DemoTransportError) as error:
            await client.place_entry(plan()["body"], HEADERS)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("synthetic", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    async def test_total_deadline_bounds_the_entire_request(self):
        calls = []
        async def handler(request):
            calls.append(request)
            await asyncio.sleep(1)
            return httpx.Response(200, json={"code":"0", "data":[]})
        client = OKXDemoTransport(CREDS, enable_demo_requests=True,
            transport=httpx.MockTransport(handler), clock=lambda:NOW)
        with patch("okx_demo_transport.TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(DemoTransportError): await client.place_entry(plan()["body"], HEADERS)
        self.assertEqual(len(calls), 1)

    async def test_redirect_does_not_follow_or_forward_credentials(self):
        client, calls = self.make(lambda r:httpx.Response(307, headers={"location":"https://evil.invalid"}))
        with self.assertRaises(DemoTransportError): await client.place_entry(plan()["body"], HEADERS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.host, "www.okx.com")

    async def test_bad_status_json_shape_size_and_sensitive_values_fail_closed(self):
        payloads = [httpx.Response(500, text="private"), httpx.Response(200, text="not-json"),
            httpx.Response(200, text="x"*65537), httpx.Response(200, json=[]),
            httpx.Response(200, json={"code":"secret", "data":[]}),
            httpx.Response(200, json={"code":"0", "data":[{},{}]}),
            httpx.Response(200, json={"code":"0", "data":[None]}),
            httpx.Response(200, json={"code":"0", "data":[{"ordId":123}]}),
            httpx.Response(200, json={"code":"0", "data":[{"ordId":"x"*129}]})]
        for response in payloads:
            client, calls = self.make(lambda r:response)
            with self.subTest(response=response.status_code), self.assertRaises(DemoTransportError):
                await client.place_entry(plan()["body"], HEADERS)
            self.assertEqual(len(calls), 1)

    async def test_individual_rejection_is_preserved_but_upstream_message_is_not(self):
        client, calls = self.make(lambda r:httpx.Response(200, json={"code":"1", "msg":"private",
            "data":[{"sCode":"51000", "sMsg":"private", "clOrdId":plan()["body"]["clOrdId"], "ordId":""}]}))
        response = await client.place_entry(plan()["body"], HEADERS)
        self.assertEqual(response["data"][0]["sCode"], "51000")
        self.assertNotIn("private", json.dumps(response))

    async def test_client_disables_proxy_redirects_and_bounds_timeout(self):
        real = httpx.AsyncClient
        captured = []
        def factory(**options):
            captured.append(options)
            return real(**options)
        client, calls = self.make()
        with patch("okx_demo_transport.httpx.AsyncClient", side_effect=factory):
            await client.place_entry(plan()["body"], HEADERS)
        self.assertIs(captured[0]["trust_env"], False)
        self.assertIs(captured[0]["follow_redirects"], False)
        self.assertEqual(captured[0]["timeout"], 5.0)

    async def test_no_environment_credentials_or_live_executor_connection(self):
        self.assertNotIn("synthetic", repr(CREDS))
        live_network_client = OKXDemoTransport(CREDS, enable_demo_requests=True)
        self.assertFalse(live_network_client.rehearsal_only)
        with tempfile.TemporaryDirectory() as temp:
            journal = DemoExecutionJournal(Path(temp)/"intent.sqlite")
            try:
                engine = DemoExecutor(journal, live_network_client, enabled=True, clock=lambda:READY+1000,
                    entry_gate=lambda p:True, recovery_gate=lambda p:True)
                source = args(); del source["now_ms"]
                with patch("okx_demo_transport.httpx.AsyncClient", side_effect=AssertionError("No network")):
                    self.assertEqual((await engine.enter(source))["phase"], "disabled")
            finally: journal.close()

    async def test_wire_transport_joins_existing_executor_with_interception_only(self):
        state = {}
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == "POST" and request.url.path.endswith("/order"):
                state["body"] = json.loads(request.content)
                body = state["body"]
                state["row"] = order({"body":body}, sz=body["sz"], side=body["side"])
                return httpx.Response(200, json={"code":"0", "data":[dict(sCode="0", clOrdId=body["clOrdId"], ordId="12345")]})
            if request.method == "GET":
                return httpx.Response(200, json={"code":"0", "data":[state["row"]]})
            return httpx.Response(200, json={"code":"0", "data":[dict(sCode="0", clOrdId=state["body"]["clOrdId"], ordId="12345")]})
        transport = OKXDemoTransport(CREDS, enable_demo_requests=True,
            transport=httpx.MockTransport(handler), clock=lambda:READY+1000)
        with tempfile.TemporaryDirectory() as temp:
            journal = DemoExecutionJournal(Path(temp)/"intent.sqlite")
            try:
                engine = DemoExecutor(journal, transport, clock=lambda:READY+1000,
                    entry_gate=lambda p:True, recovery_gate=lambda p:True, enabled=True)
                source = args(); del source["now_ms"]
                self.assertEqual((await engine.enter(source))["phase"], "entry_acknowledged_query_required")
                key = state["body"]["clOrdId"]
                self.assertEqual((await engine.refresh(key))["phase"], "entry_observed_live")
                self.assertEqual((await engine.cancel_remaining(key))["phase"], "cancel_acknowledged_query_required")
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(r.headers["x-simulated-trading"]=="1" for r in calls))
                self.assertFalse(journal.status(key)["new_entry_allowed"])
            finally: journal.close()


if __name__ == "__main__": unittest.main()
