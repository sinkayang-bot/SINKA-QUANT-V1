import test from 'node:test';
import assert from 'node:assert/strict';
import {RULES,createAccount,analyze,enter,exit,onQuote,summary,backtest,validateBars} from '../strategy/snr-engine.mjs';
import {parseCandles,createMarketClient,validateSnapshot} from '../strategy/public-market.mjs';
import {createPaperRunner} from '../strategy/paper-runner.mjs';
const T=Date.parse('2026-10-16T00:00:00Z'),M=RULES.barMs;
const bar=(i,extra={})=>({ts:T+i*M,o:100,h:100.4,l:99.6,c:100,...extra});
const history=()=>Array.from({length:80},(_,i)=>bar(i));
const longBars=()=>[...history(),bar(80,{o:100,c:101,h:101.2,l:99.9})];
const shortBars=()=>[...history(),bar(80,{o:100,c:99,h:100.1,l:98.8})];
const long=()=>analyze(longBars());
const opened=()=>{const a=createAccount();assert.equal(enter(a,long(),101,T+81*M),null);return a;};
function runnerHarness(snapshots){
  let time=T+81*M,elapsed=0,calls=0,id=0;const timers=new Map(),changes=[];
  const market={snapshot:async()=>{calls++;const value=snapshots.shift();if(value instanceof Error)throw value;return value;}};
  const runner=createPaperRunner({symbol:'BTC-USDT-SWAP',market,onChange:s=>changes.push(s),now:()=>time,monotonicNow:()=>elapsed,
    setTimer:(fn,ms)=>{timers.set(++id,{fn,ms});return id;},clearTimer:i=>timers.delete(i)});
  return {runner,timers,changes,get calls(){return calls;},advance:ms=>{time+=ms;elapsed+=ms;},at:t=>{time=t;elapsed=t-(T+81*M);}};
}
const snap=(bars=longBars(),price=101,quoteTs=T+81*M)=>({bars,price,quoteTs});
test('fixed complete strategy version and no broker mode',()=>{assert.equal(createAccount().mode,'local_paper');assert.ok(Object.isFrozen(RULES));assert.throws(()=>createAccount('UNKNOWN'));});
test('confirmed breakout uses prior range, true range and rising EMA',()=>{
  const s=long();assert.equal(s.side,1);assert.equal(s.resistance,100.4);assert.ok(Math.abs(s.atr-.8)<1e-10);assert.equal(s.readyAt,T+81*M);
  assert.equal(analyze(shortBars()).side,-1);assert.equal(analyze(history()).reason,'warmup');
});
test('flat candles generate no trade and trigger is excluded from resistance',()=>{
  assert.equal(analyze([...history(),bar(80)]).reason,'no_signal');assert.equal(long().resistance,100.4);
});
test('prefix window and exact recent live window generate identical signals',()=>{
  const extended=[...Array.from({length:40},(_,i)=>bar(i-40)),...longBars()];
  assert.deepEqual(analyze(extended),long());
});
test('invalid, inverted, duplicate, unsorted and gapped candles are rejected',()=>{
  for(const bars of [[bar(0,{c:Infinity})],[bar(0,{l:101})],[bar(0),bar(0)],[bar(1),bar(0)],[bar(0),bar(2)]])assert.throws(()=>validateBars(bars));
});
test('entry sizing includes both fees and stop slippage within 0.5% budget',()=>{
  const a=opened(),p=a.position;assert.ok(p.estimatedStopLoss<=50+1e-9);assert.ok(p.qty*p.entry<=30000);assert.equal(a.dailyEntries,1);
  exit(a,p.stop,T+81*M+1000,'stop');assert.ok(Math.abs(a.cash-9950)<1e-8);assert.ok(Math.abs(a.closed[0].pnl+50)<1e-8);
});
test('target is exactly two price-risk units after actual slipped entry',()=>{
  for(const bars of [longBars(),shortBars()]){const a=createAccount(),s=analyze(bars);assert.equal(enter(a,s,s.close,s.readyAt),null);const p=a.position;assert.ok(Math.abs(p.side*(p.target-p.entry)/(p.side*(p.entry-p.stop))-2)<1e-10);}
});
test('duplicate signal, stale signal and large entry drift cannot be traded',()=>{
  let a=createAccount();assert.equal(enter(a,long(),101,T+81*M+20001),'stale_signal');assert.equal(enter(a,long(),101,T+81*M),'duplicate_signal');
  a=createAccount();assert.equal(enter(a,long(),103,T+81*M),'entry_drift');
});
test('invalid stop direction, huge stop and tiny stop are rejected',()=>{
  for(const stop of [105,80,100.99]){const a=createAccount();assert.equal(enter(a,{...long(),stop},101,T+81*M),'stop_distance');}
});
test('only one position, no pyramiding or reversal',()=>{const a=opened();const first=structuredClone(a.position);assert.equal(enter(a,{...long(),id:'other'},101,T+81*M),'position_open');assert.deepEqual(a.position,first);});
test('both directions exit at stop and at target using net accounting',()=>{
  for(const bars of [longBars(),shortBars()])for(const reason of ['stop','target']){
    const a=createAccount(),s=analyze(bars);enter(a,s,s.close,s.readyAt);const p=a.position;onQuote(a,p[reason],s.readyAt+1000);
    assert.equal(a.position,null);assert.equal(a.closed[0].reason,reason);assert.ok(Math.abs(a.cash-(a.initial+a.closed[0].pnl))<1e-8);assert.ok(reason==='target'?a.closed[0].pnl>0:a.closed[0].pnl<0);
  }
});
test('daily mark-to-market loss limit closes exposure and halts entries',()=>{
  const a=opened();onQuote(a,90,T+81*M+1000);assert.equal(a.position,null);assert.equal(a.dailyHalt,true);assert.equal(a.closed[0].reason,'daily_loss');
  assert.equal(enter(a,{...long(),id:'next',readyAt:T+85*M},101,T+85*M),'daily_halt');
});
test('three consecutive losses halt the Taiwan calendar day',()=>{
  const a=createAccount();for(let i=0;i<3;i++){const ts=T+(81+i*4)*M;assert.equal(enter(a,{...long(),id:String(i),readyAt:ts},101,ts),null);exit(a,a.position.stop,ts+1000,'stop');}
  assert.equal(a.lossStreak,3);assert.equal(a.dailyHalt,true);
});
test('cooldown blocks fresh candidates for 15 minutes',()=>{const a=opened();exit(a,a.position.target,T+81*M+1000,'target');assert.equal(enter(a,{...long(),id:'second',readyAt:T+82*M},101,T+82*M),'cooldown');});
test('daily entries cannot exceed six and reset at Taiwan midnight',()=>{
  const a=createAccount();a.day='2026-10-16';a.dailyEntries=6;assert.equal(enter(a,long(),101,T+81*M),'daily_entries');
  const ts=Date.parse('2026-10-16T16:00:00Z');assert.equal(enter(a,{...long(),id:'newday',readyAt:ts},101,ts),null);assert.equal(a.day,'2026-10-17');assert.equal(a.dailyEntries,1);
});
test('position times out after 12 five-minute bars',()=>{const a=opened();onQuote(a,101,T+93*M);assert.equal(a.closed[0].reason,'time_limit');});
test('backtest enters at next open and ambiguous stop/target bars choose stop',()=>{
  const bars=[...longBars(),bar(81,{o:101,h:105,l:99,c:101})];const r=backtest(bars);assert.equal(r.count,1);assert.equal(r.account.closed[0].openedAt,T+81*M);assert.equal(r.account.closed[0].reason,'stop');
});
test('gap stops execute at worse open rather than ideal stop',()=>{
  const bars=[...longBars(),bar(81,{o:101,h:101.1,l:100.9,c:101}),bar(82,{o:95,h:96,l:94,c:95})];
  const r=backtest(bars);assert.equal(r.count,1);assert.equal(r.account.closed[0].exit,95*(1-RULES.slip));assert.ok(r.account.closed[0].pnl<-50);
});
test('end-of-history closes remaining position and balances reconcile',()=>{
  const r=backtest([...longBars(),bar(81,{o:101,h:101.1,l:100.9,c:101})]);assert.equal(r.account.closed[0].reason,'end_of_data');assert.equal(r.account.position,null);assert.ok(Math.abs(r.balance-(10000+r.account.closed.reduce((s,t)=>s+t.pnl,0)))<1e-8);
});
test('flat history has zero trades and zero fees',()=>{const r=backtest(Array.from({length:150},(_,i)=>bar(i)));assert.equal(r.count,0);assert.equal(r.balance,10000);assert.equal(r.maxDrawdown,0);});
test('unfinished candles are excluded; bad completed rows fail closed',()=>{
  const row=b=>[String(b.ts),String(b.o),String(b.h),String(b.l),String(b.c),'1','1','1','1'];
  assert.deepEqual(parseCandles([row(bar(1)),[...row(bar(2)).slice(0,8),'0'],row(bar(0))]),[bar(0),bar(1)]);
  assert.throws(()=>parseCandles([[String(T),'NaN','1','1','1','1','1','1','1']]));
});
test('public market client has only fixed public GET requests, never authorization',async()=>{
  const requests=[];const client=createMarketClient(async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({code:'0',data:url.includes('/ticker?')?[{instId:'BTC-USDT-SWAP',last:'101',ts:String(T)}]:[]})};});
  await client.snapshot('BTC-USDT-SWAP');assert.equal(requests.length,2);
  for(const {url,options}of requests){assert.equal(new URL(url).origin,'https://www.okx.com');assert.ok(new URL(url).pathname.startsWith('/api/v5/market/'));assert.equal(options.method,'GET');assert.equal(options.headers,undefined);assert.equal(options.credentials,'omit');}
  await assert.rejects(client.snapshot('UNKNOWN'));
});
test('stale/future quotes and stale bars are rejected',()=>{
  for(const s of [snap(longBars(),NaN),snap(longBars(),101,T),snap(longBars(),101,T+81*M+3000)])assert.throws(()=>validateSnapshot(s,T+81*M));
});
test('runner bootstrap does not retroactively trade startup breakout',async()=>{const h=runnerHarness([snap()]);await h.runner.start();assert.equal(h.runner.getState().account.position,null);assert.equal(h.runner.getState().status,'waiting_signal');h.runner.dispose();assert.equal(h.timers.size,0);});
test('new confirmed signal enters only once across repeated market polls',async()=>{
  const before=Array.from({length:81},(_,i)=>bar(i));const after=[...before,bar(81,{o:100,c:101,h:101.2,l:99.9})];
  let now=T+82*M-5000,elapsed=0,requests=0;const market={snapshot:async()=>snap(requests++===0?before:after,requests===1?100:101,now)};
  const r=createPaperRunner({symbol:'BTC-USDT-SWAP',market,now:()=>now,monotonicNow:()=>elapsed,setTimer:()=>1,clearTimer:()=>{}});
  await r.start();now+=5000;elapsed+=5000;await r.poll();assert.equal(r.getState().account.dailyEntries,1);
  now+=5000;elapsed+=5000;await r.poll();assert.equal(r.getState().account.dailyEntries,1);r.dispose();
});
test('market failure stops entries and closes paper exposure only on next valid quote',async()=>{
  let now=T+82*M-5000,elapsed=0,count=0;const before=Array.from({length:81},(_,i)=>bar(i)),after=[...before,bar(81,{o:100,c:101,h:101.2,l:99.9})];
  const r=createPaperRunner({symbol:'BTC-USDT-SWAP',now:()=>now,monotonicNow:()=>elapsed,setTimer:()=>1,clearTimer:()=>{},market:{snapshot:async()=>{count++;if(count===3)throw Error('offline');return snap(count===1?before:after,count===1?100:101,now);}}});
  await r.start();now+=5000;elapsed+=5000;await r.poll();assert.ok(r.getState().account.position);
  await r.poll();assert.equal(r.getState().active,false);assert.ok(r.getState().account.position);
  await r.poll();assert.equal(r.getState().account.position,null);assert.equal(r.getState().account.closed[0].reason,'market_error');r.dispose();
});
test('late response after dispose cannot create paper positions or schedule more polls',async()=>{
  let finish;const r=createPaperRunner({symbol:'BTC-USDT-SWAP',now:()=>T+81*M,market:{snapshot:()=>new Promise(resolve=>{finish=resolve;})},setTimer:()=>1,clearTimer:()=>{}});
  const pending=r.start();r.dispose();finish(snap());await pending;assert.equal(r.getState().active,false);assert.equal(r.getState().account.position,null);
});
test('stopping during a pending scan prevents the late scan from entering',async()=>{
  let now=T+82*M-5000,elapsed=0,finish,count=0;
  const before=Array.from({length:81},(_,i)=>bar(i)),after=[...before,bar(81,{o:100,c:101,h:101.2,l:99.9})];
  const r=createPaperRunner({symbol:'BTC-USDT-SWAP',now:()=>now,monotonicNow:()=>elapsed,setTimer:()=>1,clearTimer:()=>{},
    market:{snapshot:async()=>++count===1?snap(before,100,now):new Promise(resolve=>{finish=resolve;})}});
  await r.start();now+=5000;elapsed+=5000;const pending=r.poll();r.pause();finish(snap(after,101,now));await pending;
  assert.equal(r.getState().active,false);assert.equal(r.getState().account.position,null);r.dispose();
});
test('poll interruption stops fresh entries even if a new signal exists',async()=>{
  const before=Array.from({length:81},(_,i)=>bar(i)),after=[...before,bar(81,{o:100,c:101,h:101.2,l:99.9})];
  const h=runnerHarness([snap(before,100),snap(after,101,T+82*M)]);
  await h.runner.start();h.advance(M);await h.runner.poll();assert.equal(h.runner.getState().active,false);assert.equal(h.runner.getState().status,'interrupted');assert.equal(h.runner.getState().account.position,null);h.runner.dispose();
});
test('paper request timeout cancels polling and requires explicit restart',async()=>{
  const timers=new Map();let id=0;
  const r=createPaperRunner({symbol:'BTC-USDT-SWAP',market:{snapshot:()=>new Promise(()=>{})},setTimer:(fn,ms)=>{timers.set(++id,{fn,ms});return id;},clearTimer:i=>timers.delete(i)});
  const pending=r.start();[...timers.values()].find(t=>t.ms===10000).fn();await pending;
  assert.equal(r.getState().status,'market_error');assert.equal(r.getState().active,false);assert.equal(timers.size,0);r.dispose();
});
test('duplicate concurrent polls issue only one pair of market reads',async()=>{
  let finish,calls=0;const r=createPaperRunner({symbol:'BTC-USDT-SWAP',now:()=>T+81*M,setTimer:()=>1,clearTimer:()=>{},market:{snapshot:()=>{calls++;return new Promise(resolve=>{finish=resolve;});}}});
  const first=r.start();await r.poll();assert.equal(calls,1);finish(snap());await first;r.dispose();
});
test('a fresh quote from before the signal closed cannot become an entry fill',async()=>{
  let now=T+82*M-5000,elapsed=0,count=0;const before=Array.from({length:81},(_,i)=>bar(i)),after=[...before,bar(81,{o:100,c:101,h:101.2,l:99.9})];
  const r=createPaperRunner({symbol:'BTC-USDT-SWAP',now:()=>now,monotonicNow:()=>elapsed,setTimer:()=>1,clearTimer:()=>{},market:{snapshot:async()=>++count===1?snap(before,100,now):snap(after,101,T+82*M-1000)}});
  await r.start();now+=5000;elapsed+=5000;await r.poll();assert.equal(r.getState().account.dailyEntries,0);r.dispose();
});
