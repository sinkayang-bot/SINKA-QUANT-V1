import test from 'node:test';
import assert from 'node:assert/strict';
import {renderAssets} from '../assets-view.mjs';
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
