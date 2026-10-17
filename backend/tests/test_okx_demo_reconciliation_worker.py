"""Default-off packaging/runtime contract and structural write prohibition."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from okx_demo_transport import OKXDemoReadTransport,DemoCredentials,DemoTransportError
from okx_demo_reconciliation_worker import execute


class ReadTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_methods_and_internal_post_are_impossible(self):
        calls=[]
        def handler(req): calls.append(req); return httpx.Response(200,json=dict(code='0',data=[]))
        transport=OKXDemoReadTransport(DemoCredentials('synthetic-key','synthetic-secret','synthetic-pass'),
            enable_demo_requests=True,transport=httpx.MockTransport(handler),clock=lambda:1000000)
        for call in (lambda:transport.place_entry({},{}),lambda:transport.cancel_entry({},{}),
            lambda:transport._send('POST','/api/v5/trade/order',{}),
            lambda:transport._send('GET','/api/v5/account/config',{})):
            with self.assertRaises(DemoTransportError): await call()
        self.assertEqual(calls,[])
        self.assertEqual((await transport.account_read('config'))['code'],'0')
        self.assertEqual(len(calls),1); self.assertEqual(calls[0].method,'GET')
        self.assertEqual(calls[0].headers['x-simulated-trading'],'1')


class WorkerTests(unittest.TestCase):
    def env(self,path):
        return dict(RUN_DEMO_RECONCILIATION='1',OKX_API_KEY='synthetic-key',OKX_API_SECRET='synthetic-secret',
            OKX_API_PASSPHRASE='synthetic-pass',OKX_EXPECTED_UID='123456',DEMO_ACCOUNT_SCOPE='synthetic-v1',DEMO_LEDGER_PATH=str(path))

    def test_disabled_does_not_resolve_credentials_open_ledger_or_call_network(self):
        with patch('okx_demo_reconciliation_worker.DemoCredentials',side_effect=AssertionError('must not resolve')):
            result,code=execute({})
        self.assertEqual((result['phase'],code),('disabled',0)); self.assertEqual(result['orders_sent'],0)

    def test_missing_server_config_is_sanitized_before_any_request(self):
        result,code=execute(dict(RUN_DEMO_RECONCILIATION='1',OKX_API_KEY='private-value'))
        self.assertEqual((result['phase'],code),('configuration_missing',2))
        self.assertNotIn('private-value',json.dumps(result))

    def test_invalid_scope_or_nonpersistent_path_rejected_without_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.sqlite'; env=self.env(path); env['DEMO_ACCOUNT_SCOPE']='invalid/scope'
            result,code=execute(env)
            self.assertEqual((result['phase'],code),('failed',2)); self.assertFalse(path.exists())
            env=self.env(path); env['DEMO_LEDGER_PATH']=':memory:'
            self.assertEqual(execute(env)[0]['phase'],'failed')

    def test_one_shot_runtime_has_get_only_transport_and_default_transition_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.sqlite'; captured=[]
            async def run(runner,**kwargs):
                captured.append((runner,kwargs))
                return dict(phase='baseline_required',orders_sent=0,new_entry_allowed=False,exchange_submission_enabled=False)
            with patch('okx_demo_reconciliation_worker.DemoReconciliationRunner.run_once',new=run):
                result,code=execute(self.env(path))
            runner,kwargs=captured[0]
            self.assertIsInstance(runner.reader.transport,OKXDemoReadTransport)
            self.assertFalse(runner.lifecycle.enabled); self.assertFalse(kwargs['initialize_baseline'])
            self.assertEqual((result['phase'],code),('baseline_required',2)); self.assertTrue(path.exists())

    def test_explicit_flags_enable_only_local_transition_and_baseline_assertion(self):
        with tempfile.TemporaryDirectory() as tmp:
            captured=[]
            async def run(runner,**kwargs):
                captured.append((runner.lifecycle.enabled,kwargs['initialize_baseline']))
                return dict(phase='baseline_initialized',orders_sent=0)
            env=self.env(Path(tmp)/'ledger.sqlite'); env.update(DEMO_CYCLE_TRANSITIONS='1',DEMO_INITIALIZE_BASELINE='1')
            with patch('okx_demo_reconciliation_worker.DemoReconciliationRunner.run_once',new=run): result,code=execute(env)
            self.assertEqual(captured,[(True,True)]); self.assertEqual(code,0)
            self.assertEqual(result['orders_sent'],0)
