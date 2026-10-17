"""Intercepted GET close-detail adapter and atomic close-ledger integration."""
import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import httpx
from test_okx_demo_snapshot import Fixture,NOW
from test_okx_demo_intent import plan,order
from okx_demo_close_reader import DemoCloseReader,DemoCloseReadError


class CloseFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.plan=plan(); body=self.plan['body']; bracket=body['attachAlgoOrds'][0]
        self.entry=order(self.plan,state='filled',accFillSz='1')
        self.algo=dict(algoId='444',algoClOrdId=bracket['attachAlgoClOrdId'],instId=body['instId'],
            instType='SWAP',state='effective',ordType='oco',sz='1',side='sell',posSide='net',tdMode='isolated',
            reduceOnly='true',closeFraction='',ordIdList=['555'],subAlgoIdList=[]) | {
                k:v for k,v in bracket.items() if k!='attachAlgoClOrdId'}
        self.child=dict(ordId='555',clOrdId='',instId=body['instId'],state='filled',sz='1',accFillSz='1',
            side='sell',posSide='net',tdMode='isolated',ordType='market',algoId='444',
            algoClOrdId=bracket['attachAlgoClOrdId'],reduceOnly=True)
        self.detail_counts=dict(entry=0,algo=0,child=0)
        self.detail_mutate=None; self.detail_response=None
        self.response_override=self.override

    def override(self,request):
        path=request.url.path; q=dict(request.url.params)
        if path=='/api/v5/trade/order-algo':
            if q!={'algoClOrdId':self.plan['body']['attachAlgoOrds'][0]['attachAlgoClOrdId']}:
                raise AssertionError('Unexpected algo query')
            kind='algo'
        elif path=='/api/v5/trade/order':
            if 'clOrdId' in q:
                if q!={'instId':self.plan['body']['instId'],'clOrdId':self.plan['body']['clOrdId']}:
                    raise AssertionError('Unexpected entry query')
                kind='entry'
            else:
                if q!={'instId':self.plan['body']['instId'],'ordId':'555'}:
                    raise AssertionError('Unexpected child query')
                kind='child'
        else: return None
        self.detail_counts[kind]+=1
        if self.detail_mutate: self.detail_mutate(self,kind,self.detail_counts[kind])
        if self.detail_response:
            response=self.detail_response(kind)
            if response is not None: return response
        return httpx.Response(200,json=dict(code='0',data=[deepcopy(getattr(self,kind))],msg='private'))


class CloseReaderTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self,f,expected='12345'):
        return await DemoCloseReader(f.reader()).collect(f.plan,expected_entry_order_id=expected)

    async def test_stable_details_are_get_only_bound_to_ids_and_keep_oldest_time(self):
        f=CloseFixture(); bundle=await self.collect(f)
        self.assertEqual(f.detail_counts,dict(entry=2,algo=2,child=2))
        self.assertEqual(bundle['close']['entry']['received_at_ms'],NOW)
        self.assertEqual(bundle['snapshot']['received_at_ms'],NOW)
        self.assertEqual(bundle['close']['child']['rows'][0]['clOrdId'],'')
        self.assertEqual(bundle['close']['algo']['rows'][0]['ordIdList'],['555'])
        self.assertFalse(bundle['snapshot']['fill_audit_ready'])
        self.assertTrue(all(r.method=='GET' and r.headers['x-simulated-trading']=='1' for r in f.calls))
        for text in ('private','synthetic-key','123456'): self.assertNotIn(text,json.dumps(bundle))

    async def test_wrong_uid_prevents_all_detail_and_amount_reads(self):
        f=CloseFixture(); f.config['uid']='999'
        with self.assertRaises(DemoCloseReadError): await self.collect(f)
        self.assertEqual(sum(f.detail_counts.values()),0)
        self.assertTrue(all(r.url.path.endswith('/config') for r in f.calls))

    async def test_default_off_transport_never_calls_http(self):
        f=CloseFixture(); snapshot=f.reader(); snapshot.transport.enabled=False
        with self.assertRaises(DemoCloseReadError): await DemoCloseReader(snapshot).collect(f.plan)
        self.assertEqual(f.calls,[])

    async def test_receipt_entry_identity_side_quantity_and_state_mismatch_rejected(self):
        for change in (dict(ordId='999'),dict(clOrdId='sk'+'0'*28),dict(side='sell'),
            dict(sz='2',accFillSz='2'),dict(state='partially_filled',accFillSz='0.4')):
            f=CloseFixture(); f.entry.update(change)
            with self.subTest(change=change),self.assertRaises(DemoCloseReadError): await self.collect(f)
        f=CloseFixture()
        with self.assertRaises(DemoCloseReadError): await self.collect(f,expected='999')

    async def test_foreign_split_and_unresolved_algo_children_not_queried(self):
        for change in (dict(algoClOrdId='foreign'),dict(ordIdList=[]),dict(ordIdList=['555','556']),
            dict(subAlgoIdList=['777']),dict(ordIdList=['0'])):
            f=CloseFixture(); f.algo.update(change)
            with self.subTest(change=change),self.assertRaises(DemoCloseReadError): await self.collect(f)
            self.assertEqual(f.detail_counts['child'],0)

    async def test_untriggered_nonreducing_changed_protection_and_foreign_child_rejected(self):
        for kind,change in (('algo',dict(state='live')),('algo',dict(reduceOnly=1)),
            ('algo',dict(slTriggerPx='1')),('child',dict(algoId='999')),('child',dict(ordId='999')),
            ('child',dict(reduceOnly=False)),('child',dict(state='partially_filled',accFillSz='0.4'))):
            f=CloseFixture(); getattr(f,kind).update(change)
            with self.subTest(kind=kind,change=change),self.assertRaises(DemoCloseReadError): await self.collect(f)

    async def test_each_detail_must_remain_equal_across_passes(self):
        for target in ('entry','algo','child'):
            f=CloseFixture()
            def mutate(f,kind,count):
                if kind==target and count==2:
                    if kind=='entry': f.entry['avgPx']='60001'
                    elif kind=='algo': f.algo['sz']='2'
                    else: f.child['fee']='-0.1'
            f.detail_mutate=mutate
            with self.subTest(target=target),self.assertRaises(DemoCloseReadError): await self.collect(f)

    async def test_account_changes_and_uid_changes_during_detail_reads_rejected(self):
        for change in ('balance','uid','pending'):
            f=CloseFixture()
            def mutate(f,kind,count):
                if change=='balance': f.balance['details'][0]['eq']='9999'
                elif change=='uid': f.config['uid']='999'
                else: f.orders=[f.entry | dict(state='live',accFillSz='0')]
            f.detail_mutate=mutate
            with self.subTest(change=change),self.assertRaises(DemoCloseReadError): await self.collect(f)

    async def test_empty_ambiguous_rejected_and_redirect_detail_responses(self):
        for response in (httpx.Response(200,json=dict(code='0',data=[])),
            httpx.Response(200,json=dict(code='0',data=[{},{}])),
            httpx.Response(200,json=dict(code='1',data=[],msg='secret')),
            httpx.Response(302,headers={'Location':'https://example.com'})):
            f=CloseFixture(); f.detail_response=lambda kind:response
            with self.subTest(response=response.status_code),self.assertRaises(DemoCloseReadError): await self.collect(f)

    async def test_fixed_query_parameters_reject_injection_before_detail_requests(self):
        f=CloseFixture(); reader=DemoCloseReader(f.reader())
        for expected in ('0','1&instId=ETH','１',1):
            with self.subTest(expected=expected),self.assertRaises(DemoCloseReadError):
                await reader.collect(f.plan,expected_entry_order_id=expected)
        self.assertEqual(f.calls,[])
        for attached in ('bad','sa'+'g'*28):
            with self.assertRaises(ValueError): await f.reader().transport.lookup_algo(attached)

    async def test_readonly_key_diagnostics_do_not_upgrade_permission(self):
        f=CloseFixture(); f.config['perm']='read_only'
        bundle=await self.collect(f)
        self.assertFalse(bundle['snapshot']['order_permission_verified'])

    async def test_expiry_timeout_and_cancellation_clear_caches(self):
        f=CloseFixture(); f.detail_mutate=lambda f,k,c:setattr(f,'now',f.now+6000)
        with self.assertRaises(DemoCloseReadError): await self.collect(f)
        f=CloseFixture(); reader=DemoCloseReader(f.reader())
        await reader.collect(f.plan)
        started=asyncio.Event()
        async def stalled(*a,**kw): started.set(); await asyncio.Future()
        with patch.object(reader,'_details',side_effect=stalled),patch('okx_demo_close_reader.asyncio.timeout',wraps=lambda _:asyncio.timeouts.timeout(0.01)):
            with self.assertRaises(DemoCloseReadError): await reader.collect(f.plan)
        self.assertIsNone(reader.last_bundle); self.assertIsNone(reader.reader.last_snapshot)
        with patch.object(reader,'_details',side_effect=stalled):
            started.clear(); task=asyncio.create_task(reader.collect(f.plan)); await started.wait(); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertIsNone(reader.last_bundle); self.assertIsNone(reader.reader.last_snapshot)


class CloseLedgerIntegration(unittest.TestCase):
    def fixture(self):
        import test_okx_demo_close_audit as fixtures
        t=fixtures.CloseAuditTests('test_losing_long_close_is_not_sign_flipped')
        t.setUp(); self.addCleanup(t.doCleanups)
        snapshot,evidence,broker,engine=t.trade()
        f=CloseFixture(); f.plan=deepcopy(t.candidate['plan'])
        f.entry=deepcopy(broker.row); f.algo=deepcopy(evidence['algo']['rows'][0]); f.child=deepcopy(evidence['child']['rows'][0])
        f.fills=deepcopy(snapshot['recent_fills'])
        # Prove the reader supplies a new observation rather than using stale journal state.
        t.journal.db.execute('UPDATE demo_execution SET observed_at_ms=NULL')
        bundle=asyncio.run(DemoCloseReader(f.reader()).collect(f.plan,expected_entry_order_id='12345'))
        return t,bundle,f

    def test_http_bundle_updates_observation_and_close_ledger_atomically(self):
        t,bundle,f=self.fixture()
        result=t.audit.audit_closed(bundle['snapshot'],bundle['close'])
        self.assertEqual(t.journal.db.execute('SELECT observed_at_ms FROM demo_execution').fetchone()[0],NOW)
        self.assertEqual(result['entry_order_id'],'12345'); self.assertEqual(result['child_order_id'],'555')
        self.assertEqual(t.audit.audit_closed(bundle['snapshot'],bundle['close']),result)
        self.assertFalse(t.authority.allows(t.candidate))
        self.assertTrue(all(r.method=='GET' for r in f.calls))

    def test_failed_close_rolls_back_fresh_observation_and_new_fills(self):
        t,bundle,_=self.fixture(); bundle['close']['child']['rows'][0]['algoId']='999'
        from okx_demo_fill_audit import DemoFillAuditError
        with self.assertRaises(DemoFillAuditError): t.audit.audit_closed(bundle['snapshot'],bundle['close'])
        self.assertIsNone(t.journal.db.execute('SELECT observed_at_ms FROM demo_execution').fetchone()[0])
        self.assertIsNone(t.journal.db.execute('SELECT 1 FROM demo_closed_trade').fetchone())
        self.assertIsNone(t.journal.db.execute('SELECT 1 FROM demo_audit_fills').fetchone())

    def test_stale_owner_cannot_persist_new_observation_or_close(self):
        t,bundle,_=self.fixture(); t.now+=30001
        owner=t.owner(t.open_journal(),'second'); owner.acquire()
        from okx_demo_fill_audit import DemoFillAuditError
        with self.assertRaises(DemoFillAuditError): t.audit.audit_closed(bundle['snapshot'],bundle['close'])
        self.assertIsNone(t.journal.db.execute('SELECT observed_at_ms FROM demo_execution').fetchone()[0])
