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
const statusResponse=()=>({ok:true,json:async()=>({mode:'okx_demo_control',live_trading_enabled:false})});
test('reconnecting during a poll never strands the request lock',async()=>{
 let release;let calls=0;
 const user={getIdToken:async()=>'synthetic-token'};
 const session=createDemoControl({getUser:()=>user,fetcher:async()=>{
  calls++;if(calls===1)return new Promise(r=>release=r);return statusResponse();}});
 session.configure(base);const poll=session.request();await Promise.resolve();
 session.configure(base);await session.request();assert.equal(calls,1);
 release(statusResponse());await poll;await session.request();assert.equal(calls,2);
});
test('a command waits for the active poll and sends exactly once',async()=>{
 let release;const calls=[];const user={getIdToken:async()=>'synthetic-token'};
 const session=createDemoControl({getUser:()=>user,fetcher:async(url,opts)=>{
  calls.push(opts.method);if(calls.length===1)return new Promise(r=>release=r);
  return {ok:true,json:async()=>({accepted:true})};}});
 session.configure(base);const poll=session.request();await Promise.resolve();
 const command=session.request('start');assert.deepEqual(calls,['GET']);
 release(statusResponse());await Promise.all([poll,command]);assert.deepEqual(calls,['GET','POST']);
});
test('a waiting command is discarded if the owner logs out',async()=>{
 let release;const calls=[];const user={getIdToken:async()=>'synthetic-token'};
 const session=createDemoControl({getUser:()=>user,fetcher:async(url,opts)=>{
  calls.push(opts.method);return new Promise(r=>release=r);}});
 session.configure(base);const poll=session.request();await Promise.resolve();
 const command=session.request('start');session.clear();release(statusResponse());
 await Promise.all([poll,command]);assert.deepEqual(calls,['GET']);
});
test('an in-flight command is neither duplicated nor replayed by polling',async()=>{
 let release;let calls=0;const user={getIdToken:async()=>'synthetic-token'};
 const session=createDemoControl({getUser:()=>user,fetcher:async()=>{
  calls++;return new Promise(r=>release=r);}});
 session.configure(base);const command=session.request('start');await Promise.resolve();
 await assert.rejects(session.request('start'),/command_in_progress/);
 await session.request();assert.equal(calls,1);
 release({ok:true,json:async()=>({accepted:true})});await command;
});


test('offline ready does not imply online and includes last heartbeat',async()=>{
  const {runnerNotice}=await import('../demo-control.mjs');
  const text=runnerNotice({runner_online:false,phase:'ready',heartbeat_at:1792594233340});
  assert.match(text,/執行器離線/);assert.match(text,/22:50:33/);assert.match(text,/不代表仍在運作/);
  assert.match(runnerNotice({runner_online:true,phase:'ready',recovery_required:true}),/需先恢復核對/);
});
