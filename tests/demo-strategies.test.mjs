import test from 'node:test';
import assert from 'node:assert/strict';
import {analyzeVariant,selectDemoSignal,VERSIONS} from '../strategy/demo-strategies.mjs';
import {replay,comparePublic} from '../strategy/demo-compare.mjs';
import {readFile} from 'node:fs/promises';
const bar=(i,x={})=>({ts:i*300000,o:100,h:100.4,l:99.6,c:100,...x});
const flat=()=>Array.from({length:80},(_,i)=>bar(i));
const fast=()=>[...flat(),bar(80,{o:100.2,h:100.6,l:99.9,c:100.55})];
const retest=()=>[...flat(),bar(80,{o:100,h:101,l:99.9,c:100.9}),bar(81,{o:100.55,h:100.7,l:100.35,c:100.6})];
const mirror=bars=>bars.map(b=>({ts:b.ts,o:200-b.o,h:200-b.l,l:200-b.h,c:200-b.c}));
test('fast breakout adds a weaker closed breakout while original stays unchanged',()=>{
  const bars=fast(),before=JSON.stringify(bars);
  assert.equal(analyzeVariant(bars,'original').reason,'no_signal');
  const s=analyzeVariant(bars,'fast');assert.equal(s.side,1);assert.equal(s.readyAt,24300000);
  assert.ok(s.stop<s.close);assert.equal(selectDemoSignal(bars,'expanded').strategy_version,VERSIONS.fast);
  assert.equal(JSON.stringify(bars),before);assert.equal(analyzeVariant(mirror(bars),'fast').side,-1);
});
test('retest reclaims the previous breakout with a fixed earlier range',()=>{
  const bars=retest();assert.equal(analyzeVariant(bars,'original').reason,'no_signal');
  assert.equal(analyzeVariant(bars,'fast').reason,'no_signal');
  assert.equal(analyzeVariant(bars,'retest').side,1);
  assert.equal(analyzeVariant(mirror(bars),'retest').side,-1);
  assert.equal(selectDemoSignal(bars,'expanded').strategy_version,VERSIONS.retest);
  const broken=structuredClone(bars);broken.at(-1).c=100.3;broken.at(-1).l=100.2;
  assert.equal(analyzeVariant(broken,'retest').reason,'no_signal');
  const untouched=structuredClone(bars);untouched.at(-1).l=100.65;untouched.at(-1).o=100.66;untouched.at(-1).c=100.69;
  assert.equal(analyzeVariant(untouched,'retest').reason,'no_signal');
});
test('variants reject incomplete history, invalid candles and unsupported profile',()=>{
  for(const v of ['fast','retest']){
    assert.equal(analyzeVariant(flat(),v).reason,'warmup');
    assert.equal(analyzeVariant([...flat(),bar(80)],v).reason,'no_signal');
    assert.throws(()=>analyzeVariant([...flat(),bar(82)],v));
  }
  assert.throws(()=>selectDemoSignal(fast(),'unknown'));
  const prefix=[...flat(),...Array.from({length:30},(_,i)=>bar(80+i)),...retest().slice(-2).map((b,i)=>({...b,ts:(110+i)*300000}))];
  for(const v of ['fast','retest'])assert.deepEqual(analyzeVariant(prefix,v),analyzeVariant(prefix.slice(-81),v));
});
test('expanded priority emits one signal and historical replay is causal with fees',()=>{
  const bars=retest();
  const strong=bars.slice(0,-1);assert.equal(selectDemoSignal(strong,'expanded').strategy_version,VERSIONS.original);
  bars.push(bar(82,{o:100.6,h:100.8}));
  for(let i=83;i<100;i++)bars.push(bar(i));
  for(const v of ['original','fast','retest','expanded']){
    const r=replay(bars,'BTC-USDT-SWAP',v);assert.ok(r.count>=1);assert.ok(r.fees>0);
    assert.ok(Number.isFinite(r.pnl));assert.equal(r.bars,100);
  }
});
test('public comparison uses bounded GET history only and labels research assumptions',async()=>{
  const bars=[...flat(),...Array.from({length:120},(_,i)=>bar(i+80))],calls=[];
  const fetcher=async(url,options)=>{
    const u=new URL(url);calls.push(u);assert.equal(options.method,'GET');
    assert.equal(options.credentials,'omit');assert.equal(u.origin,'https://www.okx.com');
    assert.equal(u.pathname,'/api/v5/market/history-candles');
    const end=u.searchParams.has('after')?Number(u.searchParams.get('after')):Infinity;
    const data=bars.filter(b=>b.ts<end).slice(-100).reverse().map(b=>[String(b.ts),b.o,b.h,b.l,b.c,'1','1','1','1']);
    return {ok:true,json:async()=>({code:'0',data})};
  };
  const r=await comparePublic({fetcher,pages:2});assert.equal(calls.length,10);assert.equal(r.orders_sent,0);
  assert.equal(r.rows.length,5);assert.match(r.assumptions,/not a shared-account portfolio/);
  assert.ok(r.rows.every(x=>x.results.length===4&&x.results.every(y=>y.count===0)));
});
test('retained public candles reproduce every published research result offline',async()=>{
  const report=JSON.parse(await readFile(new URL('../strategy/research/demo-compare-20261018.json',import.meta.url),'utf8'));
  assert.equal(report.mode,'public_historical_research');assert.equal(report.orders_sent,0);
  for(const row of report.rows)for(const result of row.results)
    assert.deepEqual(replay(row.candles,row.symbol,result.variant),result);
});
