import test from 'node:test';
import assert from 'node:assert/strict';
import {controlBase,createDemoControl} from '../demo-control.mjs';
const base='https://sinka-okx-demo-control-361301529939.asia-east1.run.app';
test('only dedicated regional control origins receive login tokens',()=>{
 assert.equal(controlBase(base),base);
 for(const url of ['http://example.com','https://evil.run.app',base+'/evil',base+'?x=1',base.replace('https://','https://user@')])assert.throws(()=>controlBase(url));
});
test('owner start uses fixed command body and no redirect',async()=>{
 const calls=[];const user={getIdToken:async()=>'synthetic-token'};
 const session=createDemoControl({getUser:()=>user,fetcher:async(url,options)=>{
  calls.push({url,options});return {ok:true,json:async()=>({accepted:true})};}});
 session.configure(base);await session.request('start');
 assert.equal(calls[0].options.redirect,'error');
 assert.deepEqual(Object.keys(JSON.parse(calls[0].options.body)).sort(),['action','request_id']);
 assert.equal(JSON.parse(calls[0].options.body).action,'start');
 await assert.rejects(session.request('live'));
});
test('logout while token refreshes prevents sending a command',async()=>{
 let resolve;const user={getIdToken:()=>new Promise(r=>resolve=r)};let called=false;
 const session=createDemoControl({getUser:()=>user,fetcher:async()=>{called=true;}});
 session.configure(base);const request=session.request('start');session.clear();resolve('synthetic-token');
 await request;assert.equal(called,false);
});
test('stale responses cannot restore signed-out state',async()=>{
 let resolve;const states=[];const user={getIdToken:async()=>'synthetic-token'};
 const session=createDemoControl({getUser:()=>user,onChange:s=>states.push(s),fetcher:async()=>new Promise(r=>resolve=r)});
 session.configure(base);const request=session.request();await Promise.resolve();session.clear();
 resolve({ok:true,json:async()=>({mode:'okx_demo_control',live_trading_enabled:false})});await request;
 assert.equal(states.at(-1).phase,'signed_out');
});
