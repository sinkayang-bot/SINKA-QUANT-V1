import test from 'node:test';
import assert from 'node:assert/strict';
import {renderAssets} from '../assets-view.mjs';
import {createAssetsSession} from '../readonly-assets.mjs';
class Element {
  constructor(){this.children=[];this.textContent='';this.hidden=false;}
  replaceChildren(){this.children=[];}
  append(child){this.children.push(child);}
}
test('asset view removes financial table cells and summary on expiry or logout',()=>{
  const elements=new Map();
  const previous=globalThis.document;
  globalThis.document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
  try {
    const state={verified:true,phase:'verified',checkedAt:'2026-10-17T00:00:00Z',assets:{summary:{total_equity_usd:'PRIVATE_AMOUNT'},
      currencies:[{currency:'USDT',equity:'PRIVATE_AMOUNT',cash_balance:'1',available_balance:null,unrealized_pnl:'0'}],positions:[],pending_orders:[]}};
    for(const phase of ['expired','signed_out','server_error']){
      renderAssets(state,{});assert.equal(elements.get('asset-private').hidden,false);
      assert.equal(elements.get('asset-currencies').children.length,1);
      renderAssets({verified:false,phase},{[phase]:'已清除'});
      assert.equal(elements.get('asset-private').hidden,true);assert.equal(elements.get('asset-equity').textContent,'—');
      assert.equal(elements.get('asset-currencies').children.length,0);
    }
  } finally {globalThis.document=previous;}
});
test('hiding asset details removes amounts, currencies, positions and orders from DOM cells',()=>{
  const elements=new Map(),previous=globalThis.document;
  globalThis.document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
  const state={verified:true,phase:'verified',checkedAt:'2026-10-17T00:00:00Z',assets:{summary:{total_equity_usd:'87654.321'},
    currencies:[{currency:'USDT',equity:'87654.321',cash_balance:'1',available_balance:'1',unrealized_pnl:'0'}],
    positions:[{instrument:'BTC-USDT-SWAP',side:'net',margin_mode:'cross',contracts:'2',average_price:'60000',mark_price:'60001',unrealized_pnl:'2',currency:'USDT',notional_usd:'120002'}],
    pending_orders:[{instrument:'ETH-USDT-SWAP',side:'buy',position_side:'net',state:'live',contracts:'1',filled_contracts:'0',price:'2000'}]}};
  try{
    renderAssets(state,{});assert.equal(elements.get('asset-positions').children.length,1);
    elements.get('asset-hide-details').checked=true;renderAssets(state,{});
    assert.equal(elements.get('asset-details').hidden,true);assert.equal(elements.get('asset-equity').textContent,'—');
    for(const id of ['asset-currencies','asset-positions','asset-orders'])assert.equal(elements.get(id).children.length,0);
    const serialized=JSON.stringify([...elements]);
    for(const privateValue of ['87654','BTC-USDT-SWAP','ETH-USDT-SWAP','USDT'])assert.ok(!serialized.includes(privateValue));
    elements.get('asset-hide-details').checked=false;renderAssets(state,{});
    assert.equal(elements.get('asset-currencies').children.length,1);assert.equal(elements.get('asset-details').hidden,false);
  }finally{globalThis.document=previous;}
});
test('unhiding after expiry cannot restore private amounts or issue another request',async()=>{
  const elements=new Map(),previous=globalThis.document;let elapsed=0,calls=0;
  globalThis.document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
  const time=Date.parse('2026-10-17T00:00:00Z');
  const session=createAssetsSession({baseUrl:'https://sinka-test.run.app',now:()=>time+elapsed,monotonicNow:()=>elapsed,
    setTimer:()=>1,clearTimer:()=>{},onChange:state=>renderAssets(state,{}),fetcher:async()=>{calls++;return{ok:true,status:200,json:async()=>({
      schema_version:1,mode:'demo_readonly',trading_enabled:false,api_permissions_readonly:true,okx_connection_verified:true,
      scope:'trading_account_and_five_monitored_swaps',atomic:false,checked_at:new Date(time).toISOString(),valid_until:new Date(time+30000).toISOString(),
      summary:{total_equity_usd:'87654.321'},currencies:[],positions:[],pending_orders:[]})};}});
  try{
    session.setUser({getIdToken:async()=>'fake-test-token'});await session.refresh();
    elements.get('asset-hide-details').checked=true;renderAssets(session.getState(),{});
    elapsed=30000;elements.get('asset-hide-details').checked=false;renderAssets(session.getState(),{});
    assert.equal(elements.get('asset-private').hidden,true);assert.equal(elements.get('asset-equity').textContent,'—');
    assert.equal(calls,1);assert.equal(session.getState().assets,undefined);
  }finally{globalThis.document=previous;}
});
test('full precision toggles presentation without mutating snapshot or retaining it after logout',()=>{
  const elements=new Map(),previous=globalThis.document;
  globalThis.document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
  const state={verified:true,phase:'verified',checkedAt:'2026-10-17T00:00:00Z',assets:{summary:{total_equity_usd:'12345.67890123456789'},currencies:[],positions:[],pending_orders:[]}};
  try{
    renderAssets(state,{});assert.equal(elements.get('asset-equity').textContent,'≈ 12,345.68');
    elements.get('asset-full-precision').checked=true;renderAssets(state,{});
    assert.equal(elements.get('asset-equity').textContent,'12345.67890123456789');
    assert.equal(state.assets.summary.total_equity_usd,'12345.67890123456789');
    renderAssets({phase:'signed_out',verified:false},{signed_out:'尚未登入'});
    assert.equal(elements.get('asset-private').hidden,true);assert.equal(elements.get('asset-equity').textContent,'—');
  }finally{globalThis.document=previous;}
});
