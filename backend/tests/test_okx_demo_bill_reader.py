"""Intercepted HTTP GET bill scans and durable settlement integration."""
import asyncio
from copy import deepcopy
import json
import unittest
import httpx
from unittest.mock import patch
from test_okx_demo_snapshot import Fixture, NOW
from okx_demo_bill_reader import DemoBillReader,DemoBillReadError
from okx_demo_reads import bill_route


def billrow(i):
    return dict(billId=str(i),ts=str(NOW-6000),type='8',subType='173',ordId='',tradeId='',
        instId='BTC-USDT-SWAP',instType='SWAP',ccy='USDT',pnl='-1',fee='0',balChg='-1',sz='1',px='101')


class BillsFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.balance['details'][0]['cashBal']='10000'
        self.bills=[]; self.bill_calls=0; self.bill_mutate=None; self.bill_response=None
        self.response_override=self.override

    def override(self,request):
        if request.url.path!='/api/v5/account/bills': return None
        self.bill_calls+=1
        if self.bill_mutate: self.bill_mutate(self,request)
        if self.bill_response: return self.bill_response(request)
        q=dict(request.url.params)
        if set(q)-{'ccy','begin','end','limit','after'} or q['ccy']!='USDT' or q['limit']!='100':
            raise AssertionError('Account-wide USDT interval only')
        rows=self.bills
        if 'after' in q: rows=[row for row in rows if int(row['billId'])<int(q['after'])]
        return httpx.Response(200,json=dict(code='0',data=deepcopy(rows[:100]),msg='private'))


class BillReaderTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self,f):
        return await DemoBillReader(f.reader()).collect(begin_ms=NOW-6000)

    async def test_stable_empty_window_keeps_oldest_time_and_never_grants_entry(self):
        f=BillsFixture(); bundle=await self.collect(f)
        self.assertEqual(bundle['bills']['through_ms'],NOW)
        self.assertEqual(bundle['bills']['received_at_ms'],NOW)
        self.assertEqual(bundle['snapshot']['received_at_ms'],NOW)
        self.assertFalse(bundle['snapshot']['fill_audit_ready'])
        self.assertFalse(bundle['snapshot']['fill_history_complete'])
        self.assertEqual(bundle['snapshot']['cash_balance_usdt'],'10000')
        self.assertEqual(f.bill_calls,2)
        self.assertTrue(all(r.method=='GET' and r.headers['x-simulated-trading']=='1' for r in f.calls))
        serialized=json.dumps(bundle)
        for sensitive in ('private','123456','synthetic-key','OK-ACCESS-KEY'): self.assertNotIn(sensitive,serialized)

    async def test_cash_and_inventory_are_checked_again_after_bill_reads(self):
        for change in ('cash','position','algo'):
            f=BillsFixture()
            def mutate(f,req):
                if change=='cash': f.balance['details'][0]['cashBal']='9999'
                elif change=='position':
                    f.positions=[dict(instId='ETH-USDT-SWAP',pos='1',posSide='net',mgnMode='isolated',posId='1')]
                else:
                    f.algos['oco']=[dict(algoId='1',instId='ETH-USDT-SWAP',state='live',ordType='oco')]
            f.bill_mutate=mutate
            with self.subTest(change=change),self.assertRaises(DemoBillReadError): await self.collect(f)

    async def test_wrong_initial_uid_stops_before_any_bill_or_amount_read(self):
        f=BillsFixture(); f.config['uid']='999'
        with self.assertRaises(DemoBillReadError): await self.collect(f)
        self.assertEqual(f.bill_calls,0)
        self.assertTrue(all(r.url.path.endswith('/config') for r in f.calls))

    async def test_account_uid_change_after_bills_rejects_entire_bundle(self):
        f=BillsFixture(); f.bill_mutate=lambda f,r:f.config.update(uid='999')
        with self.assertRaises(DemoBillReadError): await self.collect(f)

    async def test_readonly_permissions_are_diagnostic_and_not_upgraded(self):
        f=BillsFixture(); f.config['perm']='read_only'
        bundle=await self.collect(f)
        self.assertFalse(bundle['snapshot']['order_permission_verified'])
        self.assertFalse(bundle['snapshot']['fill_audit_ready'])

    async def test_100_rows_require_empty_terminal_page_in_each_pass(self):
        f=BillsFixture(); f.bills=[billrow(i) for i in range(100,0,-1)]
        bundle=await self.collect(f)
        self.assertEqual([len(p['data']) for p in bundle['bills']['passes'][0]],[100,0])
        self.assertEqual(f.bill_calls,4)
        requests=[r for r in f.calls if r.url.path.endswith('/bills')]
        self.assertEqual(requests[1].url.params['after'],'1')
        self.assertNotIn('after',requests[2].url.params)

    async def test_exact_500_and_overflow_bounds(self):
        f=BillsFixture(); f.bills=[billrow(i) for i in range(500,0,-1)]
        bundle=await self.collect(f)
        self.assertEqual(len(bundle['bills']['passes'][0]),6)
        f=BillsFixture(); f.bills=[billrow(i) for i in range(501,0,-1)]
        with self.assertRaises(DemoBillReadError): await self.collect(f)

    async def test_loop_duplicate_and_unsorted_page_rejected(self):
        for rows in ([billrow(1),billrow(1)],[billrow(1),billrow(2)]):
            f=BillsFixture(); f.bills=rows
            with self.assertRaises(DemoBillReadError): await self.collect(f)
        f=BillsFixture(); f.bills=[billrow(i) for i in range(100,0,-1)]
        f.bill_response=lambda r:httpx.Response(200,json=dict(code='0',data=f.bills))
        with self.assertRaises(DemoBillReadError): await self.collect(f)

    async def test_out_of_interval_malformed_and_wrong_currency_rejected(self):
        for change in (dict(ts=str(NOW+1)),dict(ts=str(NOW-6001)),dict(ccy='BTC'),dict(pnl='NaN'),dict(billId='01')):
            f=BillsFixture(); f.bills=[billrow(1)|change]
            with self.subTest(change=change),self.assertRaises(DemoBillReadError): await self.collect(f)

    async def test_two_pass_change_and_failed_page_clear_cached_bundle(self):
        f=BillsFixture(); reader=DemoBillReader(f.reader())
        await reader.collect(begin_ms=NOW-6000)
        f.bill_response=lambda r:httpx.Response(200,json=dict(code='1',data=[],msg='secret-error'))
        with self.assertRaises(DemoBillReadError) as caught: await reader.collect(begin_ms=NOW-6000)
        self.assertNotIn('secret-error',str(caught.exception)); self.assertIsNone(reader.last_bundle)
        self.assertIsNone(reader.reader.last_snapshot)
        f=BillsFixture(); f.bills=[billrow(1)]
        def mutate(f,r):
            if f.bill_calls==2: f.bills[0]['pnl']='-2'
        f.bill_mutate=mutate
        with self.assertRaises(DemoBillReadError): await self.collect(f)

    async def test_default_off_transport_never_reaches_http(self):
        f=BillsFixture(); reader=f.reader(); reader.transport.enabled=False
        with self.assertRaises(DemoBillReadError): await DemoBillReader(reader).collect(begin_ms=NOW-6000)
        self.assertEqual(f.calls,[])

    async def test_invalid_or_expired_interval_rejected_before_requests(self):
        f=BillsFixture(); reader=DemoBillReader(f.reader())
        for begin in (True,0,NOW+1,NOW-604800001,'1'):
            with self.subTest(begin=begin),self.assertRaises(DemoBillReadError): await reader.collect(begin_ms=begin)
        self.assertEqual(f.calls,[])
        for cursor in ('0','01','-1','1&instId=BTC','１'):
            with self.subTest(cursor=cursor),self.assertRaises(ValueError): bill_route(1,2,after=cursor)

    async def test_missing_cash_and_clock_expiry_rejected(self):
        f=BillsFixture(); del f.balance['details'][0]['cashBal']
        with self.assertRaises(DemoBillReadError): await self.collect(f)
        f=BillsFixture(); f.bill_mutate=lambda f,r:setattr(f,'now',f.now+6000)
        with self.assertRaises(DemoBillReadError): await self.collect(f)

    async def test_redirection_http_error_and_response_sanitization(self):
        for status in (302,401,500):
            f=BillsFixture(); f.bill_response=lambda r:httpx.Response(status,headers={'Location':'https://example.com'},text='secret')
            with self.subTest(status=status),self.assertRaises(DemoBillReadError): await self.collect(f)
        f=BillsFixture(); f.bills=[billrow(1)|dict(extra='secret')]
        bundle=await self.collect(f)
        self.assertNotIn('extra',bundle['bills']['passes'][0][0]['data'][0])

    async def test_whole_scan_timeout_clears_success(self):
        f=BillsFixture(); reader=DemoBillReader(f.reader())
        await reader.collect(begin_ms=NOW-6000)
        async def stalled(*a,**kw): await asyncio.Future()
        with patch.object(reader,'_pages',side_effect=stalled),patch('okx_demo_bill_reader.asyncio.timeout',wraps=lambda _:asyncio.timeouts.timeout(0.01)):
            with self.assertRaises(DemoBillReadError): await reader.collect(begin_ms=NOW-6000)
        self.assertIsNone(reader.last_bundle)

    async def test_external_cancellation_clears_all_cached_success(self):
        f=BillsFixture(); reader=DemoBillReader(f.reader())
        await reader.collect(begin_ms=NOW-6000)
        started=asyncio.Event()
        async def stalled(*a,**kw):
            started.set(); await asyncio.Future()
        with patch.object(reader,'_pages',side_effect=stalled):
            task=asyncio.create_task(reader.collect(begin_ms=NOW-6000))
            await started.wait(); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertIsNone(reader.last_bundle); self.assertIsNone(reader.reader.last_snapshot)


class BillSettlementIntegration(unittest.TestCase):
    def test_intercepted_reader_bundle_settles_funding_and_daily_risk_once(self):
        import test_okx_demo_settlement as fixtures
        t=fixtures.SettlementTests('test_zero_funding_requires_complete_scans_and_cash_check')
        t.setUp(); self.addCleanup(t.doCleanups)
        snapshot,evidence,trade,broker,engine=t.prepared('-100')
        f=BillsFixture(); f.now=t.now
        f.balance['details'][0]['cashBal']=snapshot['cash_balance_usdt']
        f.fills=deepcopy(snapshot['recent_fills'])
        f.bills=deepcopy(evidence['passes'][0][0]['data'])
        reader=DemoBillReader(f.reader())
        bundle=asyncio.run(reader.collect(begin_ms=evidence['begin_ms']))
        # Fresh individual order lookup still required independently of account reads.
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        result=t.audit.settle(bundle['snapshot'],bundle['bills'])
        from decimal import Decimal
        self.assertEqual(Decimal(result['net_usdt']),Decimal(trade['trading_net_usdt'])-100)
        self.assertEqual(t.audit.settle(bundle['snapshot'],bundle['bills']),result)
        self.assertEqual(t.journal.db.execute('SELECT loss_streak FROM demo_daily').fetchone()[0],1)
        self.assertEqual(t.journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertFalse(t.authority.allows(t.candidate))
        self.assertTrue(all(r.method=='GET' for r in f.calls))
