// SINKA QUANT V1.3 — experimental SNR signal scanner (read-only)
const SNR_SYMBOLS=['BTC-USDT-SWAP','ETH-USDT-SWAP','SOL-USDT-SWAP','XRP-USDT-SWAP','DOGE-USDT-SWAP'];
const SNR_BARS=[['1m','1m'],['5m','5m'],['15m','15m']];
const snrState={results:new Map(),running:false,lastScan:0};
const snrEl=id=>document.getElementById(id);
const snrNum=x=>Number(x).toLocaleString('en-US',{maximumFractionDigits:6});
async function snrCandles(instId,bar){
 const u=new URL('https://www.okx.com/api/v5/market/candles');
 u.searchParams.set('instId',instId);u.searchParams.set('bar',bar);u.searchParams.set('limit','100');
 const response=await fetch(u,{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);
 const body=await response.json();if(body.code!=='0'||!Array.isArray(body.data))throw Error(body.msg||'invalid candles');
 // OKX newest first; discard unconfirmed candle
 return body.data.filter(x=>x[8]==='1').map(x=>({ts:Number(x[0]),o:Number(x[1]),h:Number(x[2]),l:Number(x[3]),c:Number(x[4]),v:Number(x[5])})).sort((a,b)=>a.ts-b.ts);
}
function snrAnalyze(bars){
 if(bars.length<45)return {status:'資料不足'};
 const last=bars[bars.length-1],previous=bars.slice(-41,-1),hi=Math.max(...previous.map(x=>x.h)),lo=Math.min(...previous.map(x=>x.l));
 const range=hi-lo,atr=previous.slice(-14).reduce((sum,b)=>sum+b.h-b.l,0)/14;
 if(!(atr>0&&range>0))return {status:'資料不足'};
 const threshold=atr*0.25;
 const nearHigh=Math.abs(last.c-hi)<=threshold,nearLow=Math.abs(last.c-lo)<=threshold;
 let direction='觀望',entry=null,stop=null,target=null;
 if(last.c>hi+threshold&&last.c>last.o){direction='突破做多候選';entry=last.c;stop=hi-atr;target=entry+2*(entry-stop);}
 else if(last.c<lo-threshold&&last.c<last.o){direction='跌破做空候選';entry=last.c;stop=lo+atr;target=entry-2*(stop-entry);}
 else if(nearHigh||nearLow){direction='接近關鍵區';}
 return {status:direction,price:last.c,support:lo,resistance:hi,entry,stop,target,ts:last.ts};
}
function snrRender(){
 const body=snrEl('snr-rows');if(!body)return;
 body.replaceChildren();
 for(const symbol of SNR_SYMBOLS)for(const [bar] of SNR_BARS){
 const key=symbol+':'+bar,result=snrState.results.get(key)||{status:'等待掃描'};
 const tr=document.createElement('tr');
 const values=[symbol.replace('-USDT-SWAP',''),bar,result.status,result.price==null?'—':snrNum(result.price),result.support==null?'—':snrNum(result.support),result.resistance==null?'—':snrNum(result.resistance),result.entry==null?'—':snrNum(result.entry),result.stop==null?'—':snrNum(result.stop),result.target==null?'—':snrNum(result.target)];
 for(const value of values){const td=document.createElement('td');td.textContent=value;tr.appendChild(td);}body.appendChild(tr);
 }
}
async function snrScan(){
 if(snrState.running)return;snrState.running=true;snrEl('snr-state').textContent='掃描中…';
 let ok=0,failed=0;
 try{
 // Limit concurrent requests to avoid excessive exchange traffic.
 for(const symbol of SNR_SYMBOLS){
  const results=await Promise.allSettled(SNR_BARS.map(async ([bar])=>snrAnalyze(await snrCandles(symbol,bar))));
  results.forEach((r,i)=>{const key=symbol+':'+SNR_BARS[i][0];if(r.status==='fulfilled'){snrState.results.set(key,r.value);ok++;}else{snrState.results.set(key,{status:'讀取失敗'});failed++;}});
  snrRender();
 }
 snrState.lastScan=Date.now();
 snrEl('snr-state').textContent='掃描完成 '+ok+'/15'+(failed?'，失敗 '+failed:'')+' · '+new Date().toLocaleTimeString('zh-TW');
 }finally{snrState.running=false;}
}
snrEl('snr-scan').addEventListener('click',snrScan);
snrRender();snrScan();setInterval(()=>{if(!document.hidden)snrScan()},60000);
