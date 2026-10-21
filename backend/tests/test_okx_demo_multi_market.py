"""Synthetic public universe and one-account multi-market execution checks."""
import json
import asyncio
import tempfile
from types import SimpleNamespace
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch
from test_okx_demo_trading_supervisor import SupervisorTests
from test_okx_strategy_bridge import args
from okx_demo_market_scanner import DemoMarketScanner
from okx_demo_trading_supervisor import DemoTradingSupervisor
from okx_demo_trading_worker import execute
from okx_demo_executor import DemoExecutionJournal


class MultiMarketTests(SupervisorTests):
    def enable_eth(self):
        def select(request):
            if request.url.params.get('instId')=='ETH-USDT-SWAP':
                self.f.instrument.update(instId='ETH-USDT-SWAP',ctValCcy='ETH')
                self.f.leverage[0]['instId']='ETH-USDT-SWAP'
        self.f.request_hook=select

    async def test_one_selected_altcoin_uses_same_ledger_and_blocks_second_entry(self):
        self.enable_eth()
        source=args();source['quote']['symbol']='ETH-USDT-SWAP'
        async def scan(*a): return source
        scan.last_scan={'universe':20,'eligible':20,'checked':20,'signals':1}
        self.supervisor=DemoTradingSupervisor(self.session,self.runner,scan,enabled=True,scan_markets=True)
        self.supervisor.started_at=self.f.now-2000
        result=await self.supervisor.tick()
        self.assertEqual(result['phase'],'native_protection_verified_monitor_required')
        self.assertEqual(self.session.reader.symbol,'ETH-USDT-SWAP')
        self.assertEqual(self.runner.reader.symbol,'ETH-USDT-SWAP')
        self.assertEqual(self.f.posts[0][1]['instId'],'ETH-USDT-SWAP')
        entry_spec=json.loads(self.journal.db.execute('SELECT spec FROM demo_entry_context').fetchone()[0])
        self.assertEqual(entry_spec['symbol'],'ETH-USDT-SWAP')
        baseline_spec=json.loads(self.journal.db.execute('SELECT spec FROM demo_close_context').fetchone()[0])
        self.assertEqual(baseline_spec['symbol'],'BTC-USDT-SWAP')
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_risk_entry').fetchone()[0],1)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_intent').fetchone()[0],1)
        await self.supervisor.tick()
        self.assertEqual(len(self.f.posts),1)
        with self.assertRaises(ValueError): await self.supervisor._select_market('SOL-USDT-SWAP')
        final=await self.supervisor._cleanup()
        self.assertEqual(final['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(len(self.f.posts),2)
        self.assertEqual(self.f.posts[1][1]['instId'],'ETH-USDT-SWAP')
        self.assertIs(self.f.posts[1][1]['reduceOnly'],True)
        self.assertEqual(self.f.positions,[])

    async def test_rejected_first_market_does_not_starve_next_market(self):
        self.enable_eth()
        btc=args();btc['signal']['stop']='100.9'
        eth=args();eth['quote']['symbol']='ETH-USDT-SWAP'
        sources=[{k:a[k] for k in ('strategy_version','signal','quote')} for a in (btc,eth)]
        payload={'sources':sources,'scan':dict(universe=20,eligible=20,checked=20,
            no_signal=18,expired=0,prestart=0,fetch_failed=0,signals=2)}
        class Process:
            returncode=0
            async def communicate(self):return json.dumps(payload).encode(),None
        scanner=DemoMarketScanner()
        self.supervisor=DemoTradingSupervisor(self.session,self.runner,scanner,enabled=True,scan_markets=True)
        self.supervisor.started_at=self.f.now-2000
        with patch('okx_demo_market_scanner.asyncio.create_subprocess_exec',return_value=Process()):
            first=await self.supervisor.tick()
            self.assertEqual(first['gate_reason'],'signal_stop_distance')
            self.assertEqual(self.f.posts,[])
            second=await self.supervisor.tick()
        self.assertEqual(second['selected_symbol'],'ETH-USDT-SWAP')
        self.assertEqual(second['phase'],'native_protection_verified_monitor_required')
        self.assertEqual(len(self.f.posts),1)
        self.assertEqual(self.f.posts[0][1]['instId'],'ETH-USDT-SWAP')
        self.assertEqual(scanner.last_scan['deferred_candidates'],1)

    async def test_unsupported_altcoin_configuration_skips_without_reservation(self):
        async def scan(*a):
            result=args();result['quote']['symbol']='ETH-USDT-SWAP';return result
        scan.excluded=set()
        self.supervisor=DemoTradingSupervisor(self.session,self.runner,scan,enabled=True,scan_markets=True)
        self.supervisor.started_at=self.f.now-2000
        result=await self.supervisor.tick()
        self.assertEqual(result['phase'],'market_preflight_blocked')
        self.assertEqual(scan.excluded,{'ETH-USDT-SWAP'})
        self.assertEqual(self.session.reader.symbol,'BTC-USDT-SWAP')
        self.assertEqual(self.f.posts,[])
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_risk_entry').fetchone()[0],0)

    async def test_stop_during_altcoin_preflight_never_dispatches(self):
        self.enable_eth();old_hook=self.f.request_hook
        def hook(request):
            old_hook(request)
            if request.url.params.get('instId')=='ETH-USDT-SWAP': self.supervisor.stop()
        self.f.request_hook=hook;self.supervisor.scan_markets=True
        self.assertFalse(await self.supervisor._select_market('ETH-USDT-SWAP'))
        self.assertEqual(self.f.posts,[])


class ScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scanner_does_not_inherit_secrets_and_ignores_excluded_candidate(self):
        payload={'sources':[{'strategy_version':'SNR-5M-1','signal':{},'quote':{'symbol':'ETH-USDT-SWAP'}}],
            'scan':dict(universe=20,eligible=20,checked=20,no_signal=19,expired=0,prestart=0,fetch_failed=0,signals=1)}
        captured={}
        class Process:
            returncode=0
            async def communicate(self): return json.dumps(payload).encode(),None
        async def spawn(*a,**k): captured.update(k);captured['argv']=a;return Process()
        scanner=DemoMarketScanner()
        with patch('okx_demo_market_scanner.asyncio.create_subprocess_exec',side_effect=spawn),patch.dict('os.environ',{'OKX_API_KEY':'private','NODE_OPTIONS':'private'}):
            self.assertEqual((await scanner('BTC-USDT-SWAP',1))['quote']['symbol'],'ETH-USDT-SWAP')
            scanner.excluded.add('ETH-USDT-SWAP')
            self.assertIsNone(await scanner('BTC-USDT-SWAP',1))
        self.assertEqual(set(captured['env']),{'PATH'})
        self.assertEqual(scanner.last_scan['excluded'],1)
        with patch('okx_demo_market_scanner.asyncio.create_subprocess_exec',side_effect=spawn):
            await DemoMarketScanner(profile='expanded')('BTC-USDT-SWAP',1)
            self.assertEqual(captured['argv'][-1],'--expanded')
        payload['scan'].update(breakout_not_met=18,trend_not_met=1,warmup=0,invalid_stop=0)
        with patch('okx_demo_market_scanner.asyncio.create_subprocess_exec',side_effect=spawn):
            await scanner('BTC-USDT-SWAP',1)
            self.assertEqual(scanner.last_scan['breakout_not_met'],18)
            payload['scan']['breakout_not_met']=17
            with self.assertRaises(ValueError): await scanner('BTC-USDT-SWAP',1)

    async def test_deferred_candidate_returns_on_new_candle_and_cache_is_bounded(self):
        source={'strategy_version':'SNR-5M-1','signal':{'id':'24000000'},'quote':{'symbol':'BTC-USDT-SWAP'}}
        payload={'sources':[source],'scan':dict(universe=20,eligible=20,checked=20,
            no_signal=19,expired=0,prestart=0,fetch_failed=0,signals=1)}
        class Process:
            returncode=0
            async def communicate(self):return json.dumps(payload).encode(),None
        scanner=DemoMarketScanner()
        with patch('okx_demo_market_scanner.asyncio.create_subprocess_exec',return_value=Process()):
            first=await scanner('BTC-USDT-SWAP',1);scanner.defer(first)
            self.assertIsNone(await scanner('BTC-USDT-SWAP',1))
            source['signal']['id']='24300000'
            self.assertIsNotNone(await scanner('BTC-USDT-SWAP',1))
            self.assertEqual(scanner.deferred,set())
        for n in range(100):
            scanner.defer(dict(source,signal={'id':str(n)}))
        self.assertLessEqual(len(scanner.deferred),60)

    async def test_cancelled_scanner_kills_and_reaps_child(self):
        started=asyncio.Event();calls=[]
        class Process:
            returncode=None
            async def communicate(self):
                started.set();await asyncio.Event().wait()
            def kill(self): calls.append('kill')
            async def wait(self): calls.append('wait');self.returncode=-9
        async def spawn(*a,**k): return Process()
        with patch('okx_demo_market_scanner.asyncio.create_subprocess_exec',side_effect=spawn):
            task=asyncio.create_task(DemoMarketScanner()('BTC-USDT-SWAP',1))
            await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(calls,['kill','wait'])

    async def test_scanner_rejects_unknown_symbols_or_unbounded_status(self):
        class Process:
            returncode=0
            async def communicate(self): return b'{"sources":[],"scan":{"universe":9000}}',None
        async def spawn(*a,**k): return Process()
        with patch('okx_demo_market_scanner.asyncio.create_subprocess_exec',side_effect=spawn):
            with self.assertRaises(ValueError): await DemoMarketScanner()('BTC-USDT-SWAP',1)


class PublicUniverseTests(unittest.TestCase):
    def test_liquidity_filters_and_twenty_market_scan_use_only_public_gets(self):
        script=r'''
        import assert from 'node:assert/strict';
        import {eligibleMarkets,scanMarkets,noSignalReason} from './strategy/demo-market-scan.mjs';
        import {analyze} from './strategy/snr-engine.mjs';
        import {SCAN_SYMBOLS as SYMBOLS} from './strategy/demo-market-scan.mjs';
        const now=1800000301000, started=now-2000;
        const ins=SYMBOLS.map(instId=>({instId,instType:'SWAP',state:'live',ctType:'linear',settleCcy:'USDT',
          ctValCcy:instId.split('-')[0],listTime:String(now-100*86400000),ctVal:'1',lotSz:'1',minSz:'1',
          tickSz:'0.1',maxMktSz:'10000',maxStopSz:'10000',upcChg:[]}));
        const quotes=SYMBOLS.map((instId,i)=>({instId,last:'101',bidPx:'100.99',askPx:'101.01',
          high24h:'102',low24h:'99',volCcy24h:String(1000000-i),ts:String(now)}));
        assert.equal(eligibleMarkets(ins,quotes,now).length,20);
        assert.equal(eligibleMarkets(ins,quotes,now)[0].symbol,SYMBOLS[0]);
        for(const change of [{state:'suspend'},{settleCcy:'USDC'},{listTime:String(now-86400000)},{upcChg:[{}]}])
          assert.equal(eligibleMarkets([Object.assign({},ins[0],change)],[quotes[0]],now).length,0);
        for(const change of [{askPx:'103'},{volCcy24h:'1'},{ts:String(now-16000)},{low24h:'1'}])
          assert.equal(eligibleMarkets([ins[0]],[Object.assign({},quotes[0],change)],now).length,0);
        assert.equal(eligibleMarkets([ins[0],ins[0]],[quotes[0]],now).length,0);
        let calls=0,active=0,max=0;
        const end=Math.floor(now/300000)*300000;
        const candles=Array.from({length:100},(_,i)=>[String(end-(100-i)*300000),'100','100.4','99.6','100','1','1','1','1']);
        candles.at(-1)[2]='101.2';candles.at(-1)[4]='101';
        const fetcher=async(url,opts)=>{
          assert.equal(opts.method,'GET');assert.equal(opts.credentials,'omit');assert.equal(opts.redirect,'error');
          const u=new URL(url);assert.equal(u.origin,'https://www.okx.com');calls++;active++;max=Math.max(max,active);
          await new Promise(resolve=>setTimeout(resolve,1));active--;
          let data;
          if(u.pathname.endsWith('/instruments'))data=ins;
          else if(u.pathname.endsWith('/tickers'))data=quotes;
          else if(u.pathname.endsWith('/candles'))data=candles;
          else if(u.pathname.endsWith('/ticker'))data=quotes.filter(q=>q.instId===u.searchParams.get('instId'));
          else throw Error('Unexpected request');
          return {ok:true,json:async()=>({code:'0',data})};
        };
        const result=await scanMarkets({startedAt:started,fetcher,clock:()=>now});
        assert.equal(result.scan.checked,20);assert.equal(result.sources.length,20);
        assert.equal(calls,42);assert.ok(max<=8);
        const old=await scanMarkets({startedAt:now,fetcher,clock:()=>now});
        assert.equal(old.sources.length,0);assert.equal(old.scan.prestart,20);
        const flat=Array.from({length:81},(_,i)=>({ts:i*300000,o:100,h:100.4,l:99.6,c:100}));
        assert.equal(noSignalReason(analyze(flat),flat),'breakout_not_met');
        assert.equal(noSignalReason(analyze(flat.slice(0,80)),flat.slice(0,80)),'warmup');
        const rejected={reason:'no_signal',support:99,resistance:101,atr:1};
        assert.equal(noSignalReason(rejected,[{c:101.25}]),'breakout_not_met');
        assert.equal(noSignalReason(rejected,[{c:101.26}]),'trend_not_met');
        assert.equal(noSignalReason(rejected,[{c:98.74}]),'trend_not_met');
        candles.at(-1)[2]='100.4';candles.at(-1)[4]='100';
        const quiet=await scanMarkets({startedAt:started,fetcher,clock:()=>now});
        assert.equal(quiet.scan.no_signal,20);assert.equal(quiet.scan.breakout_not_met,20);
        assert.equal(quiet.scan.trend_not_met,0);assert.equal(quiet.sources.length,0);
        console.log(JSON.stringify(SYMBOLS));
        '''
        result=subprocess.run(['node','--input-type=module','-e',script],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        from okx_risk import SYMBOLS
        self.assertEqual(set(json.loads(result.stdout)),set(SYMBOLS))


class RestartMarketTests(unittest.TestCase):
    def test_recovery_reader_uses_durable_altcoin_intent_before_any_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ledger.sqlite'
            journal=DemoExecutionJournal(path)
            journal.db.execute('INSERT INTO demo_intent VALUES(?,?,1,NULL)',('existing',json.dumps({'body':{'instId':'ETH-USDT-SWAP'}})))
            journal.close();seen=[]
            def reader(transport,**kwargs):
                seen.append(kwargs['symbol']);return SimpleNamespace(scope='synthetic-v1')
            env=dict(RUN_DEMO_TRADING='1',DEMO_TRADING_AUTHORIZED='1',DEMO_SCAN_MARKETS='20',
                OKX_API_KEY='synthetic',OKX_API_SECRET='synthetic',OKX_API_PASSPHRASE='synthetic',
                OKX_EXPECTED_UID='123',DEMO_ACCOUNT_SCOPE='synthetic-v1',DEMO_LEDGER_PATH=str(path))
            with patch('okx_demo_trading_worker.DemoSnapshotReader',side_effect=reader), \
                 patch('okx_demo_trading_worker.DemoEntryAuthority',side_effect=ValueError('stop before runtime')):
                self.assertEqual(execute(env)[1],2)
            self.assertEqual(seen,['ETH-USDT-SWAP','ETH-USDT-SWAP'])
            seen.clear();env['DEMO_SCAN_MARKETS']='1'
            with patch('okx_demo_trading_worker.DemoSnapshotReader',side_effect=reader):
                self.assertEqual(execute(env)[1],2)
            self.assertEqual(seen,[])
