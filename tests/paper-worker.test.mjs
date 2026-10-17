import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {createAccount,analyze,enter,exit,RULES} from '../strategy/snr-engine.mjs';
import {restoreAccount} from '../strategy/account-checkpoint.mjs';
import {createPaperRunner} from '../strategy/paper-runner.mjs';
import {openPaperStore} from '../worker/paper-store.mjs';
import {options,runWorker} from '../worker/paper-worker.mjs';
const T=Date.parse('2026-10-17T00:00:00Z'),M=RULES.barMs,SYMBOL='BTC-USDT-SWAP';
const bars=Array.from({length:81},(_,i)=>({ts:T+i*M,o:100,h:i===80?101.2:100.4,l:99.6,c:i===80?101:100}));
const candidate=()=>analyze(bars);
function account(){const a=createAccount(SYMBOL);enter(a,candidate(),101,T+81*M);return a;}
function temp(fn){const dir=mkdtempSync(join(tmpdir(),'sinka-worker-'));try{return fn(join(dir,'paper.sqlite'));}finally{rmSync(dir,{recursive:true,force:true});}}
async function tempAsync(fn){const dir=mkdtempSync(join(tmpdir(),'sinka-worker-'));try{return await fn(join(dir,'paper.sqlite'));}finally{rmSync(dir,{recursive:true,force:true});}}
const snapshot=()=>({bars,price:101,quoteTs:T+81*M});
test('valid ledger restores open position without dropping cash, risk counters or dedup id',()=>{
  const a=account(),restored=restoreAccount(JSON.parse(JSON.stringify(a)),SYMBOL);assert.deepEqual(restored,a);restored.cash=0;assert.notEqual(a.cash,0);
});
test('closed-fill accounting restores fees and net P&L',()=>{const a=account();exit(a,a.position.stop,T+81*M+1000,'stop');assert.deepEqual(restoreAccount(a,SYMBOL),a);});
test('tampered cash, quantity, mode, version, daily counters and risk are rejected',()=>{
  for(const mutate of [a=>a.cash++,a=>a.position.qty*=2,a=>a.mode='live',a=>a.version='unknown',a=>a.dailyEntries=0,a=>a.position.stop=a.position.entry,a=>a.position.riskBudget*=3,a=>a.lastSignal='injected']){
    const a=account();mutate(a);assert.throws(()=>restoreAccount(a,SYMBOL));
  }
  assert.throws(()=>restoreAccount(account(),'ETH-USDT-SWAP'));
});
test('wrong fill balances and fabricated event payloads fail validation',()=>{
  const a=account();exit(a,a.position.target,T+81*M+1000,'target');a.closed[0].balance++;assert.throws(()=>restoreAccount(a,SYMBOL));
  const b=account();b.events.push({ts:T,type:'entry',reason:'x',secret:'not-allowed'});assert.throws(()=>restoreAccount(b,SYMBOL));
});
test('SQLite checkpoints persist across close/reopen and identical payloads do not multiply revisions',()=>temp(path=>{
  let store=openPaperStore(path,{symbol:SYMBOL,now:()=>T});assert.equal(store.load(),null);
  const a=account();assert.equal(store.save(a),1);assert.equal(store.save(a),1);store.close();
  store=openPaperStore(path,{symbol:SYMBOL,now:()=>T});assert.deepEqual(store.load(),a);assert.equal(store.revision,1);store.close();
}));
test('second worker cannot acquire an unexpired database lease',()=>temp(path=>{
  const one=openPaperStore(path,{symbol:SYMBOL,now:()=>T});try{assert.throws(()=>openPaperStore(path,{symbol:SYMBOL,now:()=>T}),/held/);}finally{one.close();}
}));
test('expired owner cannot write after lease takeover and cannot release the new owner lease',()=>temp(path=>{
  let time=T;const one=openPaperStore(path,{symbol:SYMBOL,now:()=>time});one.load();one.save(createAccount(SYMBOL));
  time+=30001;const two=openPaperStore(path,{symbol:SYMBOL,now:()=>time});assert.deepEqual(two.load(),createAccount(SYMBOL));
  assert.throws(()=>one.save(createAccount(SYMBOL)),/lease lost/);one.close();two.renew();two.close();
}));
test('expired owner cannot extend its own expired lease',()=>temp(path=>{
  let time=T;const store=openPaperStore(path,{symbol:SYMBOL,now:()=>time});time+=30000;assert.throws(()=>store.renew(),/lease lost/);store.close();
}));
test('database corruption or unknown schema does not silently reset to a fresh account',()=>temp(path=>{
  let store=openPaperStore(path,{symbol:SYMBOL,now:()=>T});store.load();store.save(account());store.close();
  let db=new DatabaseSync(path);db.prepare("UPDATE account SET payload='{}'").run();db.close();
  store=openPaperStore(path,{symbol:SYMBOL,now:()=>T});assert.throws(()=>store.load(),/checksum/);store.close();
  db=new DatabaseSync(path);db.exec('PRAGMA user_version=99');db.close();assert.throws(()=>openPaperStore(path,{symbol:SYMBOL,now:()=>T}),/version/);
}));
test('clock rollback stops lease renewal rather than extending ownership',()=>temp(path=>{
  let time=T;const store=openPaperStore(path,{symbol:SYMBOL,now:()=>time});time--;assert.throws(()=>store.renew(),/backwards/);store.close();
}));
test('restored open position is flattened using fresh price, without a new entry',async()=>{
  const restored=account(),cash=restored.cash;
  const r=createPaperRunner({symbol:SYMBOL,initialAccount:restored,market:{snapshot:async()=>snapshot()},now:()=>T+81*M,
    setTimer:()=>1,clearTimer:()=>{},onChange:()=>{}});
  await r.start();const s=r.getState();assert.equal(s.account.position,null);assert.equal(s.active,false);assert.equal(s.status,'recovered');
  assert.equal(s.account.closed.length,1);assert.equal(s.account.closed[0].reason,'recovery');assert.notEqual(s.account.cash,cash);r.dispose();
});
test('failed recovery quote preserves the old position for later recovery',async()=>{
  const restored=account();const r=createPaperRunner({symbol:SYMBOL,initialAccount:restored,market:{snapshot:async()=>{throw Error('offline');}},now:()=>T+81*M,setTimer:()=>1,clearTimer:()=>{}});
  await r.start();assert.deepEqual(r.getState().account.position,restored.position);assert.equal(r.getState().active,false);r.dispose();
});
test('checkpoint write failure stops strategy before any public request',async()=>{
  let calls=0;const r=createPaperRunner({symbol:SYMBOL,checkpoint:()=>{throw Error('disk full');},market:{snapshot:async()=>{calls++;return snapshot();}},setTimer:()=>1,clearTimer:()=>{}});
  await r.start();assert.equal(calls,0);assert.equal(r.getState().status,'storage_error');assert.equal(r.getState().active,false);
});
test('once-mode worker persists startup state, releases lock and makes no paper trade',async()=>tempAsync(async path=>{
  const logs=[];assert.equal(await runWorker({symbol:SYMBOL,db:path,maxSeconds:null,once:true},{market:{snapshot:async()=>snapshot()},now:()=>T+81*M,log:x=>logs.push(x),processSignals:false}),0);
  const store=openPaperStore(path,{symbol:SYMBOL,now:()=>T+81*M});const a=store.load();assert.equal(a.position,null);assert.equal(a.dailyEntries,0);store.close();
  assert.ok(logs.some(l=>l.status==='waiting_signal'));assert.ok(logs.every(l=>!JSON.stringify(l).includes('Authorization')));
}));
test('worker restart keeps existing daily halt instead of resetting virtual funds',async()=>tempAsync(async path=>{
  const a=account();for(let i=0;i<3;i++){
    if(i){const ts=T+(81+i*4)*M;enter(a,{...candidate(),id:String(bars.at(-1).ts+i*M),readyAt:ts},101,ts);}
    exit(a,a.position.stop,T+(81+i*4)*M+1000,'stop');
  }
  let now=T+90*M;const store=openPaperStore(path,{symbol:SYMBOL,now:()=>now});store.load();store.save(a);store.close();
  const shifted=bars.map(b=>({...b,ts:b.ts+9*M}));
  await runWorker({symbol:SYMBOL,db:path,maxSeconds:null,once:true},{market:{snapshot:async()=>({bars:shifted,price:101,quoteTs:now})},now:()=>now,processSignals:false,log:()=>{}});
  const next=openPaperStore(path,{symbol:SYMBOL,now:()=>now});const saved=next.load();assert.equal(saved.cash,a.cash);assert.equal(saved.dailyHalt,true);assert.equal(saved.lossStreak,3);next.close();
}));
test('worker options require explicit local database, known symbol and bounded test duration',()=>{
  assert.throws(()=>options([]));assert.throws(()=>options(['--db','x','--symbol','UNKNOWN']));assert.throws(()=>options(['--db','x','--max-seconds','0']));assert.throws(()=>options(['--live']));
  assert.equal(options(['--db','/tmp/paper.sqlite','--once']).once,true);
});
test('failed SQLite transaction rolls back checkpoint and keeps in-memory revision unchanged',()=>temp(path=>{
  const store=openPaperStore(path,{symbol:SYMBOL,now:()=>T});store.load();store.save(createAccount(SYMBOL));
  const db=new DatabaseSync(path);db.exec("CREATE TRIGGER fail_save BEFORE UPDATE ON account BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END;");
  assert.throws(()=>store.save(account()));assert.equal(store.revision,1);assert.equal(store.load().position,null);
  db.exec('DROP TRIGGER fail_save');db.close();assert.equal(store.save(account()),2);store.close();
}));
test('checkpoint failure after arithmetic entry prevents every subsequent poll',async()=>{
  let time=T+82*M-5000,elapsed=0,calls=0;
  const before=Array.from({length:81},(_,i)=>({ts:T+i*M,o:100,h:100.4,l:99.6,c:100}));
  const after=[...before,{ts:T+81*M,o:100,h:101.2,l:99.6,c:101}];
  const r=createPaperRunner({symbol:SYMBOL,now:()=>time,monotonicNow:()=>elapsed,setTimer:()=>1,clearTimer:()=>{},
    checkpoint:a=>{if(a.position)throw Error('disk failed');},market:{snapshot:async()=>{calls++;return {bars:calls===1?before:after,price:calls===1?100:101,quoteTs:time};}}});
  await r.start();time+=5000;elapsed+=5000;await r.poll();assert.equal(r.getState().status,'storage_error');assert.equal(r.getState().active,false);
  await r.start();await r.poll();assert.equal(calls,2);
});
test('lease fencing survives actual process termination without a graceful release',async()=>tempAsync(async path=>{
  const {spawn}=await import('node:child_process');const {pathToFileURL}=await import('node:url');
  const moduleUrl=pathToFileURL(join(process.cwd(),'worker/paper-store.mjs')).href;
  const child=spawn(process.execPath,['--input-type=module','-e',`import {openPaperStore} from ${JSON.stringify(moduleUrl)}; const s=openPaperStore(process.argv[1],{symbol:'BTC-USDT-SWAP',now:()=>${T}}); console.log('ready');setInterval(()=>{},1000);`,path],{stdio:['ignore','pipe','pipe']});
  const exited=new Promise(resolve=>child.once('exit',resolve));
  try{
    await new Promise((resolve,reject)=>{child.once('error',reject);child.stdout.once('data',resolve);child.once('exit',()=>reject(Error('child exited before lease')));});
    assert.throws(()=>openPaperStore(path,{symbol:SYMBOL,now:()=>T}),/held/);
    child.kill('SIGKILL');await exited;
    assert.throws(()=>openPaperStore(path,{symbol:SYMBOL,now:()=>T+1000}),/held/);
    const next=openPaperStore(path,{symbol:SYMBOL,now:()=>T+30001});assert.equal(next.load(),null);next.close();
  }finally{if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await exited;}}
}));
test('recovery continues to carry its recovery label through an unavailable first quote',async()=>{
  let calls=0;const r=createPaperRunner({symbol:SYMBOL,initialAccount:account(),now:()=>T+81*M,setTimer:()=>1,clearTimer:()=>{},market:{snapshot:async()=>{if(++calls===1)throw Error('offline');return snapshot();}}});
  await r.start();await r.poll();assert.equal(r.getState().account.closed[0].reason,'recovery');assert.equal(r.getState().status,'recovered');r.dispose();
});
test('worker heartbeat exposes quote freshness without amounts or credentials',async()=>tempAsync(async path=>{
  let heartbeat,ready;const started=new Promise(resolve=>{ready=resolve;});const logs=[];let time=T+81*M;
  const pending=runWorker({symbol:SYMBOL,db:path,maxSeconds:1,once:false},{market:{snapshot:async()=>snapshot()},now:()=>time,
    log:row=>{logs.push(row);if(row.status==='waiting_signal')ready();},processSignals:false,setHeartbeat:fn=>{heartbeat=fn;return 1;},clearHeartbeat:()=>{}});
  await started;for(let i=0;i<6;i++)heartbeat();assert.equal(logs.at(-1).event,'heartbeat');assert.equal(logs.at(-1).quoteFresh,true);
  time+=20000;for(let i=0;i<6;i++)heartbeat();assert.equal(logs.at(-1).quoteFresh,false);
  assert.ok(logs.every(row=>!('cash' in row)&&!('Authorization' in row)&&!('price' in row)));
  assert.equal(await pending,0);
}));
