// V1.4: browser-only deterministic candle backtest. No exchange orders.
const BT={bars:[],trades:[],equity:[],symbol:'BTC-USDT-SWAP',bar:'5m',busy:false};
const bt=id=>document.getElementById(id);
function simulate(bars){
 const start=10000,risk=0.005,fee=0.0005,slip=0.0002;
 let balance=start,peak=start,maxDD=0;const trades=[],equity=[];
 // Signal uses only completed bars; entry occurs at NEXT bar open.
 for(let i=42;i<bars.length-1;){
  const history=bars.slice(i-40,i),trigger=bars[i],hi=Math.max(...history.map(b=>b.h)),lo=Math.min(...history.map(b=>b.l));
  const atr=history.slice(-14).reduce((s,b)=>s+b.h-b.l,0)/14;
  const long=trigger.c>hi+atr*.25&&trigger.c>trigger.o,short=trigger.c<lo-atr*.25&&trigger.c<trigger.o;
  if(!atr||(!long&&!short)){i++;continue;}
  const side=long?1:-1,raw=bars[i+1].o,entry=raw*(1+side*slip);
  const stop=side===1?hi-atr:lo+atr,dist=side*(entry-stop);
  if(dist<=0){i++;continue;}
  const target=entry+side*2*dist,qty=(balance*risk)/dist;
  // Conservative: if TP and SL are both touched within one candle, assume SL first.
  let exit=null,exitIndex=i+1,reason='End of data';
  for(let j=i+1;j<bars.length;j++){const b=bars[j],stopHit=side===1?b.l<=stop:b.h>=stop,targetHit=side===1?b.h>=target:b.l<=target;
   if(stopHit){exit=stop*(1-side*slip);exitIndex=j;reason='Stop';break;}
   if(targetHit){exit=target*(1-side*slip);exitIndex=j;reason='Target';break;}
  }
  if(exit===null){exit=bars[bars.length-1].c*(1-side*slip);exitIndex=bars.length-1;}
  const pnl=qty*side*(exit-entry)-fee*qty*(entry+exit);
  balance+=pnl;peak=Math.max(peak,balance);maxDD=Math.max(maxDD,(peak-balance)/peak);
  trades.push({time:bars[i+1].ts,side:side===1?'LONG':'SHORT',entry,exit,pnl,reason,balance});
  equity.push({time:bars[exitIndex].ts,balance});
  i=exitIndex+1; // One position at a time.
 }
 return {trades,equity,balance,maxDD,start};
}

const BT_STORAGE_KEY='sinka_quant_v17_last_backtest';
function btPaintTrades(trades){
 const body=bt('bt-rows');body.replaceChildren();
 for(const t of trades.slice(-30).reverse()){
  const row=document.createElement('tr');
  for(const val of [new Date(t.time).toLocaleString('zh-TW'),t.side,Number(t.entry).toFixed(5),Number(t.exit).toFixed(5),Number(t.pnl).toFixed(2),t.reason]){
   const td=document.createElement('td');td.textContent=val;row.appendChild(td);
  }body.appendChild(row);
 }
}
function btDisplay(data){
 const {out,symbol,bar,first,last}=data;
 const wins=out.trades.filter(t=>t.pnl>0),losses=out.trades.filter(t=>t.pnl<0);
 const gp=wins.reduce((n,t)=>n+t.pnl,0),gl=-losses.reduce((n,t)=>n+t.pnl,0),pnl=out.balance-out.start;
 const put=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v;};
 put('ov-pnl',(pnl>=0?'+':'')+pnl.toFixed(2));
 put('ov-return',((out.balance/out.start-1)*100).toFixed(2)+'%');
 put('ov-win',out.trades.length?(wins.length/out.trades.length*100).toFixed(1)+'%':'—');
 put('ov-trades',String(out.trades.length));put('ov-pf',gl>0?(gp/gl).toFixed(2):'—');put('ov-dd',(out.maxDD*100).toFixed(2)+'%');
 put('bt-trades',String(out.trades.length));
 put('bt-win',out.trades.length?(wins.length/out.trades.length*100).toFixed(1)+'%':'—');
 put('bt-return',((out.balance/out.start-1)*100).toFixed(2)+'%');
 put('bt-dd',(out.maxDD*100).toFixed(2)+'%');put('bt-pf',gl>0?(gp/gl).toFixed(2):'—');
 const info=symbol+' / '+bar+' · '+new Date(first).toLocaleString('zh-TW')+' ～ '+new Date(last).toLocaleString('zh-TW')+' · 初始資金 '+out.start+' USDT';
 put('ov-info','最近一次歷史回測（本機儲存）：'+info);
 put('bt-state','回測結果：'+info);
 const el=bt('ov-pnl');if(el)el.className='value '+(pnl>=0?'up':'down');
 btPaintTrades(out.trades);
}
function btRestore(){
 try{const raw=localStorage.getItem(BT_STORAGE_KEY);if(!raw)return;const data=JSON.parse(raw);
  if(data?.version!==1||!Array.isArray(data.out?.trades)||!Number.isFinite(data.out?.balance))return;
  BT.trades=data.out.trades;BT.equity=data.out.equity||[];btDisplay(data);
 }catch(e){console.warn('Cannot restore saved backtest',e);}
}
function btExport(){
 if(!BT.trades.length){alert('請先執行有交易結果的回測');return;}
 const rows=[['時間','方向','進場','出場','淨損益USDT','出場原因','結算權益'],...BT.trades.map(t=>[new Date(t.time).toISOString(),t.side,t.entry,t.exit,t.pnl,t.reason,t.balance])];
 const csv='\uFEFF'+rows.map(row=>row.map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(',')).join('\r\n');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
 const a=document.createElement('a');a.href=url;a.download='sinka-quant-backtest.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
}

async function backtest(){
 if(BT.busy)return;BT.busy=true;bt('bt-state').textContent='回測中…';
 try{
 const symbol=bt('bt-symbol').value,bar=bt('bt-bar').value;
 // Paginate historical completed candles; exchange caps requests.
 let before='',all=new Map();
 for(let page=0;page<6;page++){
  const url=new URL('https://www.okx.com/api/v5/market/history-candles');
  url.searchParams.set('instId',symbol);url.searchParams.set('bar',bar);url.searchParams.set('limit','100');
  if(before)url.searchParams.set('after',before);
  const res=await fetch(url,{cache:'no-store'});if(!res.ok)throw Error('HTTP '+res.status);
  const j=await res.json();if(j.code!=='0'||!Array.isArray(j.data))throw Error(j.msg||'OKX API error');
  if(!j.data.length)break;
  for(const x of j.data){if(x[8]==='1')all.set(Number(x[0]),{ts:Number(x[0]),o:+x[1],h:+x[2],l:+x[3],c:+x[4]});}
  const oldest=Math.min(...j.data.map(x=>Number(x[0])));if(!Number.isFinite(oldest)||String(oldest)===before)break;before=String(oldest);
 }
 const bars=[...all.values()].sort((a,b)=>a.ts-b.ts);if(bars.length<50)throw Error('已收盤 K 棒不足');
 const out=simulate(bars);BT.bars=bars;BT.trades=out.trades;BT.equity=out.equity;
 const snapshot={version:1,symbol,bar,first:bars[0].ts,last:bars[bars.length-1].ts,out:{start:out.start,balance:out.balance,maxDD:out.maxDD,trades:out.trades,equity:out.equity}};
 btDisplay(snapshot);
 try{localStorage.setItem(BT_STORAGE_KEY,JSON.stringify(snapshot));}catch(e){bt('bt-state').textContent+='（本機儲存失敗）';}
 }catch(e){bt('bt-state').textContent='回測失敗：'+String(e.message||e);}finally{BT.busy=false;}
}
bt('bt-run').addEventListener('click',backtest);
bt('bt-export').addEventListener('click',btExport);
btRestore();
