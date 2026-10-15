// SINKA QUANT V1.8 — local browser paper trading only; NEVER sends orders.
(function(){
'use strict';
const KEY='sinka_quant_paper_v18',START=10000,FEE=.0005,SLIP=.0002,MAX_LEVERAGE=3,RISK=.005;
const $=id=>document.getElementById(id);
const fmt=n=>Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
let state={cash:START,position:null,closed:[]},busy=false;
try{const saved=JSON.parse(localStorage.getItem(KEY)||'null');if(saved&&Number.isFinite(saved.cash)&&saved.cash>=0&&Array.isArray(saved.closed)&&(!saved.position||Number.isFinite(saved.position.qty)))state=saved;}catch(e){}
function save(){try{localStorage.setItem(KEY,JSON.stringify(state));}catch(e){$('paper-status').textContent='儲存失敗，請勿依賴此資料';}}
function quote(symbol){const q=typeof market!=='undefined'?market.get(symbol):null;const price=Number(q?.last);const age=Date.now()-Number(q?.ts||0);return Number.isFinite(price)&&price>0&&age<20000?price:null;}
function pnl(p,price){return p.qty*p.side*(price-p.entry)-p.qty*price*FEE;}
function summary(){
 const p=state.position,price=p?quote(p.symbol):null,unreal=p&&price?pnl(p,price):0;
 const equity=state.cash+unreal;
 const wins=state.closed.filter(x=>x.pnl>0).length;
 const values={'paper-equity':fmt(equity),'paper-cash':fmt(state.cash),'paper-unreal':p?(price?fmt(unreal):'行情過期'):'0.00','paper-win':state.closed.length?(100*wins/state.closed.length).toFixed(1)+'%':'—','paper-count':String(state.closed.length),'paper-pnl':fmt(state.cash-START)};
 Object.entries(values).forEach(([id,v])=>{const e=$(id);if(e)e.textContent=v;});
 $('paper-position').textContent=p?(p.symbol+' · '+(p.side===1?'做多':'做空')+' · '+fmt(p.qty)+' 單位 · 進場 '+fmt(p.entry)+' · 停損 '+fmt(p.stop)):'目前無模擬持倉';
 $('paper-close').disabled=!p||!price;
 $('paper-open').disabled=!!p;
 const body=$('paper-history');body.replaceChildren();
 for(const t of state.closed.slice(-15).reverse()){const tr=document.createElement('tr');for(const v of [new Date(t.time).toLocaleString('zh-TW'),t.symbol,t.side===1?'LONG':'SHORT',fmt(t.pnl),t.reason]){const td=document.createElement('td');td.textContent=v;tr.appendChild(td);}body.appendChild(tr);}
}
function open(){
 if(busy||state.position)return;const symbol=$('paper-symbol').value,side=Number($('paper-side').value),price=quote(symbol),stop=Number($('paper-stop').value);
 if(!price){$('paper-status').textContent='行情未連線或已過期，無法建立模擬倉位';return;}
 const entry=price*(1+side*SLIP),dist=side*(entry-stop);
 if(!Number.isFinite(stop)||stop<=0||dist<=0){$('paper-status').textContent='停損必須位於進場價格的虧損方向';return;}
 const equity=state.cash,qtyByRisk=(equity*RISK)/(dist+entry*FEE+stop*FEE),qtyByLev=(equity*MAX_LEVERAGE)/entry,qty=Math.min(qtyByRisk,qtyByLev);
 const fee=qty*entry*FEE;if(!(qty>0)||fee>=state.cash)return;
 state.cash-=fee;state.position={symbol,side,entry,stop,qty,time:Date.now()};
 save();$('paper-status').textContent='已建立本機模擬倉位；僅在此網頁開啟且行情有效時監測停損';summary();
}
function close(reason='手動平倉'){
 if(busy||!state.position)return;const p=state.position,price=quote(p.symbol);if(!price){$('paper-status').textContent='行情過期，暫停模擬平倉';return;}
 const exit=price*(1-p.side*SLIP),net=pnl(p,exit);
 state.cash+=net;state.closed.push({time:Date.now(),symbol:p.symbol,side:p.side,entry:p.entry,exit,pnl:net-p.qty*p.entry*FEE,reason});
 state.position=null;save();$('paper-status').textContent='已模擬平倉：'+reason;summary();
}
function tick(){const p=state.position;if(p){const price=quote(p.symbol);if(price&&(p.side===1?price<=p.stop:price>=p.stop))close('停損（網頁開啟期間）');}summary();}
$('paper-open').addEventListener('click',open);$('paper-close').addEventListener('click',()=>close());
$('paper-reset').addEventListener('click',()=>{if(!confirm('確定清除本機所有模擬交易紀錄並重設 10,000 USDT？'))return;state={cash:START,position:null,closed:[]};save();summary();});
summary();setInterval(tick,3000);
})();
