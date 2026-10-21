import test from 'node:test';
import assert from 'node:assert/strict';
import {rememberLogin, loginPersistence, changeLoginPersistence} from '../auth-persistence.mjs';

test('remember login defaults on and honors an explicit opt-out on reopening', async () => {
  const data = new Map();
  const storage = {getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,v)};
  assert.equal(rememberLogin(storage), true);
  const calls=[]; const auth={};
  const sdk={browserLocalPersistence:'local',inMemoryPersistence:'memory',
    setPersistence:async(a,p)=>calls.push([a,p])};
  await changeLoginPersistence({sdk,auth,remember:false,storage});
  assert.equal(rememberLogin(storage),false);
  assert.equal(loginPersistence(sdk,rememberLogin(storage)),'memory');
  await changeLoginPersistence({sdk,auth,remember:true,storage});
  assert.deepEqual(loginPersistence(sdk,rememberLogin(storage)),['local','memory']);
  assert.deepEqual(calls,[[auth,'memory'],[auth,'local']]);
  assert.deepEqual([...data.values()],['true']);
});

test('failed SDK migration preserves the previous saved preference', async () => {
  let writes=0;
  const storage={getItem:()=> 'false',setItem:()=>writes++};
  const sdk={setPersistence:async()=>{throw Error('storage unavailable');}};
  await assert.rejects(changeLoginPersistence({sdk,auth:{},remember:true,storage}));
  assert.equal(writes,0);
  assert.equal(rememberLogin(storage),false);
});

test('blocked preference storage does not prevent SDK session migration', async () => {
  const storage={getItem:()=>{throw Error();},setItem:()=>{throw Error();}};
  let called=false;
  assert.equal(rememberLogin(storage),true);
  await changeLoginPersistence({sdk:{setPersistence:async()=>{called=true;}},auth:{},remember:true,storage});
  assert.equal(called,true);
});
