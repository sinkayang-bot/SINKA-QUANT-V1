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
 const wins=out.trades.filter(t=>t.pnl>0),losses=out.trades.filter(t=>t.pnl<0);
 const grossProfit=wins.reduce((s,t)=>s+t.pnl,0),grossLoss=-losses.reduce((s,t)=>s+t.pnl,0);
 bt('bt-trades').textContent=String(out.trades.length);
 bt('bt-win').textContent=out.trades.length?(wins.length/out.trades.length*100).toFixed(1)+'%':'—';
 bt('bt-return').textContent=((out.balance/out.start-1)*100).toFixed(2)+'%';
 bt('bt-dd').textContent=(out.maxDD*100).toFixed(2)+'%';
 bt('bt-pf').textContent=grossLoss>0?(grossProfit/grossLoss).toFixed(2):'—';
 bt('bt-state').textContent='回測完成：'+bars.length+' 根已收盤 K 棒 · '+new Date(bars[0].ts).toLocaleString('zh-TW')+' ～ '+new Date(bars[bars.length-1].ts).toLocaleString('zh-TW');
 const body=bt('bt-rows');body.replaceChildren();
 for(const t of out.trades.slice(-30).reverse()){const row=document.createElement('tr');for(const val of [new Date(t.time).toLocaleString('zh-TW'),t.side,t.entry.toFixed(5),t.exit.toFixed(5),t.pnl.toFixed(2),t.reason]){const td=document.createElement('td');td.textContent=val;row.appendChild(td);}body.appendChild(row);}
 }catch(e){bt('bt-state').textContent='回測失敗：'+String(e.message||e);}finally{BT.busy=false;}
}
bt('bt-run').addEventListener('click',backtest);
