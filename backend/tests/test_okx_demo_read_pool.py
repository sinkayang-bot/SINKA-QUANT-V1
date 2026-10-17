"""Reproduce burst-sensitive GET failures without any exchange connection."""
import asyncio
import unittest
from unittest.mock import patch

import httpx
from okx_demo_transport import OKXDemoTransport,OKXDemoReadTransport,DemoTransportError
from test_okx_demo_snapshot import Fixture
from test_okx_demo_intent import plan


class ReadPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_reuses_one_client_and_caps_a_burst_sensitive_server_at_two(self):
        fixture=Fixture(); active=0; peak=0; clients=[]
        async def handler(request):
            nonlocal active,peak
            active+=1; peak=max(peak,active)
            try:
                if active>2: raise httpx.ConnectError('synthetic burst rejected')
                await asyncio.sleep(0.005)
                return fixture.handler(request)
            finally: active-=1
        reader=fixture.reader(); reader.transport._transport=httpx.MockTransport(handler)
        original=httpx.AsyncClient
        def client(*args,**kwargs):
            value=original(*args,**kwargs); clients.append(value); return value
        with patch('okx_demo_transport.httpx.AsyncClient',side_effect=client):
            snapshot=await reader.collect()
        self.assertTrue(snapshot['complete'])
        self.assertLessEqual(peak,2); self.assertEqual(active,0)
        self.assertEqual(len(clients),1); self.assertTrue(clients[0].is_closed)
        self.assertIsNone(reader.transport._read_client)

    async def test_cancelled_snapshot_closes_pool_and_all_inflight_reads(self):
        fixture=Fixture(); reader=fixture.reader(); blocked=asyncio.Event(); active=0
        async def handler(request):
            nonlocal active
            if request.url.path.endswith('/config'): return fixture.handler(request)
            active+=1; blocked.set()
            try: await asyncio.Event().wait()
            finally: active-=1
        reader.transport._transport=httpx.MockTransport(handler)
        task=asyncio.create_task(reader.collect())
        await asyncio.wait_for(blocked.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(active,0)
        self.assertFalse(reader.transport._read_active)
        self.assertIsNone(reader.transport._read_client)
        reader.transport._transport=httpx.MockTransport(fixture.handler)
        self.assertTrue((await reader.collect())['complete'])

    async def test_read_pool_rejects_writes_and_does_not_reuse_cookies(self):
        fixture=Fixture(); transport=fixture.reader().transport; calls=[]
        def handler(request):
            calls.append(request)
            self.assertNotIn('cookie',request.headers)
            return httpx.Response(200,headers={'set-cookie':'private-cookie=synthetic'},json={'code':'0','data':[]})
        transport._transport=httpx.MockTransport(handler)
        async with transport.read_session():
            for _ in range(2): await transport.account_read('config')
            with self.assertRaises(DemoTransportError):
                await transport.place_entry(plan()['body'],{'x-simulated-trading':'1'})
            with self.assertRaises(DemoTransportError):
                async with transport.read_session(): pass
        self.assertEqual(len(calls),2)
        self.assertFalse(transport._read_active)

    async def test_disabled_pool_and_low_level_readonly_transport_never_send(self):
        with patch('okx_demo_transport.httpx.AsyncClient',side_effect=AssertionError('Unexpected client')):
            with self.assertRaises(DemoTransportError):
                async with OKXDemoTransport().read_session(): pass
            with self.assertRaises(DemoTransportError):
                await OKXDemoReadTransport()._send_request('POST','/api/v5/trade/order',plan()['body'])

    async def test_failed_pooled_get_preserves_safe_status_without_retry(self):
        fixture=Fixture(); transport=fixture.reader().transport; calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(429,json={'msg':'private-provider-message'})
        transport._transport=httpx.MockTransport(handler)
        with self.assertRaises(DemoTransportError) as caught:
            async with transport.read_session(): await transport.account_read('config')
        self.assertEqual(caught.exception.http_status,429)
        self.assertNotIn('private-provider-message',str(caught.exception))
        self.assertEqual(len(calls),1)
        self.assertIsNone(transport._read_client)
