// Optional cloud paper account connector. Never store the API token.
(function(){
'use strict';
const $=id=>document.getElementById(id);
const url=$('cloud-url'),token=$('cloud-token'),status=$('cloud-status');
const key='sinka_cloud_endpoint_v1';
try{url.value=localStorage.getItem(key)||'';}catch(_){}
function endpoint(){const value=url.value.trim().replace(/\/+$/,'');if(!/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(value)){throw Error('請輸入有效 HTTPS API 網址，不含路徑');}return value;}
function credentials(){const value=token.value.trim();if(!value)throw Error('請輸入雲端 API Token');return value;}
function number(v){return typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—';}
async function call(path,method='GET',body){
 const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);
 try{
  const response=await fetch(endpoint()+path,{method,headers:{Authorization:'Bearer '+credentials(),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:controller.signal,cache:'no-store'});
  if(!response.ok)throw Error('API '+response.status);
  return await response.json();
 }finally{clearTimeout(timeout);}
}
let connected=false;
async function refresh(){
 try{
  const [state,perf]=await Promise.all([call('/api/state'),call('/api/performance')]);
  connected=true;
  $('cloud-equity').textContent=number(state.equity);
  $('cloud-pnl').textContent=number(perf.realized_pnl);
  $('cloud-win').textContent=perf.win_rate_pct==null?'—':perf.win_rate_pct.toFixed(1)+'%';
  $('cloud-trades').textContent=String(perf.closed_trades);
  $('cloud-position').textContent=state.position?(state.position.symbol+' '+(state.position.side===1?'LONG':'SHORT')+' · '+number(state.position.qty)+' 單位 · 進場 '+number(state.position.entry)+' · 停損 '+number(state.position.stop)):'目前沒有雲端模擬持倉';
  status.textContent='已連線至雲端模擬帳戶（不是真實交易）';
  try{localStorage.setItem(key,endpoint());}catch(_){}
 }catch(e){connected=false;status.textContent='連線失敗：'+e.message+'。本機模擬帳戶不受影響。';}
}
$('cloud-connect').addEventListener('click',refresh);
$('cloud-open').addEventListener('click',async()=>{
 if(!connected){status.textContent='請先連線雲端';return;}
 if(!confirm('確認在雲端建立模擬倉位？不會下真實訂單。'))return;
 try{await call('/api/open','POST',{symbol:$('cloud-symbol').value,side:Number($('cloud-side').value),stop:Number($('cloud-stop').value)});await refresh();}catch(e){status.textContent='模擬開倉失敗：'+e.message;}
});
$('cloud-close').addEventListener('click',async()=>{
 if(!connected||!confirm('確認雲端模擬平倉？'))return;
 try{await call('/api/close','POST');await refresh();}catch(e){status.textContent='模擬平倉失敗：'+e.message;}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&connected)refresh();});
setInterval(()=>{if(connected&&!document.hidden)refresh();},15000);
})();
