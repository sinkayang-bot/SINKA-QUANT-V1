import test from 'node:test';
import assert from 'node:assert/strict';
import {statusEndpoint, verifiedResult, createStatusSession} from '../readonly-session.mjs';

const BASE = 'https://sinka-status-test.run.app';
const START = Date.parse('2026-10-17T00:00:00Z');
const payload = (time = START) => ({mode:'demo_readonly', okx_connection_verified:true,
  api_permissions_readonly:true, trading_enabled:false,
  checks:Object.fromEntries(['account_config','balances','positions','instruments','pending_orders'].map(k=>[k,true])),
  checked_at:new Date(time).toISOString(), valid_until:new Date(time+30000).toISOString()});
function setup(options = {}) {
  let time = START, counter = 0;
  const changes = [], timers = new Map(), calls = [];
  const s = createStatusSession({baseUrl:BASE, now:()=>time,
    setTimer:(fn,ms)=>{const id=++counter;timers.set(id,{fn,ms});return id;},
    clearTimer:id=>timers.delete(id), onChange:x=>changes.push(x),
    fetcher:async (...args)=>{calls.push(args);return {ok:true,json:async()=>payload()};}, ...options});
  const user = {uid:'private-user',getIdToken:async()=> 'private-token'};
  return {s,user,changes,timers,calls,setTime:x=>{time=x;}};
}

test('no configured service does not request token or send any network call', async()=>{
  const h=setup({baseUrl:null});
  h.s.setUser({getIdToken:()=>assert.fail('must not request token')}); await h.s.refresh();
  assert.equal(h.calls.length,0); assert.equal(h.s.getState().phase,'service_pending');
});
test('signed out cannot query',async()=>{const h=setup();await h.s.refresh();assert.equal(h.calls.length,0);});
test('only a fixed HTTPS Cloud Run origin can receive tokens',()=>{
  for(const url of ['http://x.run.app','https://evil.example','https://x.run.app/other','https://name:pw@x.run.app','https://x.run.app?key=secret'])
    assert.throws(()=>statusEndpoint(url));
  assert.equal(statusEndpoint(BASE),BASE+'/api/okx/status');
});
test('verified request uses GET, no caching, no cookies, no redirects',async()=>{
  const h=setup();h.s.setUser(h.user);await h.s.refresh();
  const [url,options]=h.calls[0]; assert.equal(url,BASE+'/api/okx/status');
  assert.equal(options.method,'GET');assert.equal(options.headers.Authorization,'Bearer private-token');
  assert.equal(options.cache,'no-store');assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');
  assert.equal(h.s.getState().verified,true);
  assert.equal(JSON.stringify(h.changes).includes('private-'),false);
});
test('logout immediately discards verified evidence',async()=>{
  const h=setup();h.s.setUser(h.user);await h.s.refresh();h.s.setUser(null);
  assert.deepEqual(h.s.getState(),{phase:'signed_out',verified:false});assert.equal(h.timers.size,0);
});
test('expired state is cleared after a background tab resumes',async()=>{
  const h=setup();h.s.setUser(h.user);await h.s.refresh();h.setTime(START+31000);h.s.checkExpiry();
  assert.deepEqual(h.s.getState(),{phase:'expired',verified:false});
});
test('old response after logout cannot restore verified status',async()=>{
  let complete;const h=setup({fetcher:()=>new Promise(resolve=>{complete=resolve;})});
  h.s.setUser(h.user);const pending=h.s.refresh();await Promise.resolve();await Promise.resolve();
  h.s.setUser(null);complete({ok:true,json:async()=>payload()});await pending;
  assert.equal(h.s.getState().phase,'signed_out');
});
test('old response after account change cannot restore verified status',async()=>{
  let complete;const h=setup({fetcher:()=>new Promise(resolve=>{complete=resolve;})});
  h.s.setUser(h.user);const pending=h.s.refresh();await Promise.resolve();await Promise.resolve();
  h.s.setUser({uid:'other',getIdToken:async()=>'other-token'});
  complete({ok:true,json:async()=>payload()});await pending;assert.equal(h.s.getState().phase,'signed_in');
});
test('token resolved after logout is never sent',async()=>{
  let resolveToken;const h=setup();h.s.setUser({getIdToken:()=>new Promise(resolve=>{resolveToken=resolve;})});
  const pending=h.s.refresh();h.s.setUser(null);resolveToken('private-token');await pending;assert.equal(h.calls.length,0);
});
test('duplicate click does not issue a duplicate request',async()=>{
  let complete;let calls=0;const h=setup({fetcher:()=>{calls++;return new Promise(r=>{complete=r;});}});
  h.s.setUser(h.user);const first=h.s.refresh();await h.s.refresh();await Promise.resolve();
  complete({ok:true,json:async()=>payload()});await first;assert.equal(calls,1);
});
test('stalled token request times out and clears checking state',async()=>{
  const h=setup();h.s.setUser({getIdToken:()=>new Promise(()=>{})});const pending=h.s.refresh();
  [...h.timers.values()].find(t=>t.ms===40000).fn();await pending;
  assert.equal(h.s.getState().phase,'timeout');assert.equal(h.calls.length,0);
});
for(const [code,phase] of [[401,'login_expired'],[403,'forbidden'],[429,'busy'],[503,'server_error'],[504,'timeout']])
  test(`HTTP ${code} cannot retain a verified state`,async()=>{
    let calls=0;
    const h=setup({fetcher:async()=>++calls===1?{ok:true,status:200,json:async()=>payload()}:{ok:false,status:code}});
    h.s.setUser(h.user);await h.s.refresh();assert.equal(h.s.getState().verified,true);
    await h.s.refresh();
    assert.deepEqual(h.s.getState(),{phase,verified:false,failureStage:'response',httpStatus:code});
  });
for (const [stage,phase] of [['token','login_unavailable'],['request','network_error'],['response','invalid_result']])
  test(`failure at ${stage} is classified without retaining raw errors`,async()=>{
    const fail=()=>{throw new Error('private-token secret-account-detail');};
    const h=setup({fetcher:stage==='request'?fail:async()=>({ok:true,status:200,json:fail})});
    h.s.setUser(stage==='token'?{getIdToken:fail}:h.user); await h.s.refresh();
    assert.equal(h.s.getState().phase,phase);assert.equal(h.s.getState().failureStage,stage);
    assert.equal(h.s.getState().verified,false);assert.ok(!JSON.stringify(h.changes).includes('private-token'));
  });
test('conflicting, incomplete and stale responses fail validation',()=>{
  for(const data of [null,{...payload(),trading_enabled:true},{...payload(),api_permissions_readonly:false},
    {...payload(),checks:{balances:true}},payload(START-31000),payload(START+6000),
    {...payload(),valid_until:new Date(START+60000).toISOString()}]) assert.equal(verifiedResult(data,START),null);
});
test('exchange extra fields never enter frontend state',async()=>{
  const h=setup({fetcher:async()=>({ok:true,json:async()=>({...payload(),secret:'private-secret',balance:12345})})});
  h.s.setUser(h.user);await h.s.refresh();assert.equal(h.s.getState().verified,true);
  assert.equal(JSON.stringify(h.s.getState()).includes('private-secret'),false);
  assert.equal('balance' in h.s.getState(),false);
});
