import test from 'node:test';
import assert from 'node:assert/strict';
import {verifiedAssets,createAssetsSession} from '../readonly-assets.mjs';
import {diagnosticSummary} from '../readonly-diagnostics.mjs';
const now=Date.parse('2026-10-17T00:00:00Z');
const payload=()=>({schema_version:1,mode:'demo_readonly',trading_enabled:false,api_permissions_readonly:true,
  okx_connection_verified:true,scope:'trading_account_and_five_monitored_swaps',atomic:false,
  checked_at:new Date(now).toISOString(),valid_until:new Date(now+30000).toISOString(),
  summary:{total_equity_usd:'1234.567890123456789',uid:'PRIVATE_UID'},
  currencies:[{currency:'USDT',equity:'1234.5',cash_balance:'1200',available_balance:null,unrealized_pnl:'34.5',key:'PRIVATE_KEY'}],
  positions:[],pending_orders:[]});
function setup(fetcher=async()=>({ok:true,status:200,json:async()=>payload()})) {
  let time=now,count=0;const timers=new Map(),changes=[];
  const session=createAssetsSession({baseUrl:'https://sinka-test.run.app',fetcher,now:()=>time,
    setTimer:(fn,ms)=>{timers.set(++count,{fn,ms});return count;},clearTimer:id=>timers.delete(id),onChange:s=>changes.push(s)});
  return {session,changes,timers,setTime:v=>{time=v;},user:{getIdToken:async()=>'PRIVATE_TOKEN'}};
}
test('asset parser preserves exact decimal strings and nulls while stripping unknown fields',()=>{
  const result=verifiedAssets(payload(),now);
  assert.equal(result.assets.summary.total_equity_usd,'1234.567890123456789');
  assert.equal(result.assets.currencies[0].available_balance,null);
  assert.ok(!JSON.stringify(result).includes('PRIVATE'));
});
test('conflicting, oversized, stale, injected or incomplete snapshots are rejected',()=>{
  for(const bad of [{...payload(),trading_enabled:true},{...payload(),atomic:true},
    {...payload(),scope:'all_accounts'},{...payload(),valid_until:new Date(now-1).toISOString()},
    {...payload(),currencies:[{...payload().currencies[0],currency:'<img onerror=alert(1)>'}]},
    {...payload(),summary:{total_equity_usd:'NaN'}},{...payload(),positions:[{}]},
    {...payload(),pending_orders:Array(2001).fill({})}]) assert.equal(verifiedAssets(bad,now),null);
});
test('pending fill comparison does not round high-precision contract quantities',()=>{
  const data=payload();data.pending_orders=[{instrument:'BTC-USDT-SWAP',side:'buy',position_side:'net',state:'partially_filled',
    contracts:'9007199254740993.00000001',filled_contracts:'9007199254740993.00000000',price:null}];
  assert.ok(verifiedAssets(data,now));
  data.pending_orders[0].filled_contracts=data.pending_orders[0].contracts;
  assert.equal(verifiedAssets(data,now),null);
});
test('asset controller uses only owner bearer GET and clears all amounts on logout and expiry',async()=>{
  const calls=[];const h=setup(async(url,options)=>{calls.push([url,options]);return{ok:true,status:200,json:async()=>payload()};});
  h.session.setUser(h.user);await h.session.refresh();
  assert.equal(calls[0][0],'https://sinka-test.run.app/api/okx/assets');
  assert.equal(calls[0][1].method,'GET');assert.equal(calls[0][1].cache,'no-store');
  assert.equal(h.session.getState().assets.summary.total_equity_usd,'1234.567890123456789');
  const diagnostic=diagnosticSummary(h.session.getState(),{signedIn:true,serviceConfigured:true},now);
  assert.ok(!diagnostic.includes('1234.567890123456789'));
  h.setTime(now+30000);h.session.checkExpiry();assert.equal(h.session.getState().assets,undefined);
  h.session.setUser(null);assert.deepEqual(h.session.getState(),{phase:'signed_out',verified:false});
});
test('late asset responses cannot restore financial data after account changes',async()=>{
  let finish;const h=setup(()=>new Promise(resolve=>{finish=resolve;}));
  h.session.setUser(h.user);const pending=h.session.refresh();await Promise.resolve();await Promise.resolve();
  h.session.setUser({getIdToken:async()=>'OTHER_TOKEN'});
  finish({ok:true,status:200,json:async()=>payload()});await pending;
  assert.equal(h.session.getState().assets,undefined);assert.equal(h.session.getState().phase,'signed_in');
});
test('a failed subsequent asset query never retains the previous snapshot',async()=>{
  let calls=0;const h=setup(async()=>++calls===1?{ok:true,status:200,json:async()=>payload()}:{ok:false,status:503});
  h.session.setUser(h.user);await h.session.refresh();h.setTime(now+30001);await h.session.refresh();
  assert.equal(h.session.getState().assets,undefined);assert.equal(h.session.getState().phase,'server_error');
});
