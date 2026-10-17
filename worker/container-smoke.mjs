import assert from 'node:assert/strict';
import {runWorker} from './paper-worker.mjs';
import {openPaperStore} from './paper-store.mjs';
const now=Date.now(),period=300000,last=Math.floor(now/period)*period-period;
const bars=Array.from({length:81},(_,i)=>({ts:last-(80-i)*period,o:100,h:100.4,l:99.6,c:100}));
const db='/data/smoke.sqlite';
const code=await runWorker({db,symbol:'BTC-USDT-SWAP',once:true,maxSeconds:null},{processSignals:false,now:()=>now,
  market:{snapshot:async()=>({bars,price:100,quoteTs:now})},log:()=>{}});
assert.equal(code,0);
const store=openPaperStore(db,{symbol:'BTC-USDT-SWAP',now:()=>now});
try{const a=store.load();assert.equal(a.cash,10000);assert.equal(a.closed.length,0);assert.equal(a.position,null);}
finally{store.close();}
console.log('Packaged paper worker: persistent checkpoint and non-root execution passed; no network calls.');
