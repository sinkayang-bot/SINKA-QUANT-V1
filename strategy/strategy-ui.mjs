import {RULES,backtest} from './snr-engine.mjs?v=strategy-1';
import {createMarketClient} from './public-market.mjs?v=strategy-1';
import {createPaperRunner} from './paper-runner.mjs?v=strategy-1';
const $=id=>document.getElementById(id),client=createMarketClient();
const number=(n,d=2)=>Number.isFinite(n)?n.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const time=n=>n?new Date(n).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'}):'—';
const reasons={stop:'停損',target:'2R 停利',time_limit:'持倉滿 60 分鐘',daily_loss:'每日虧損達限制',
  end_of_data:'回測資料結束',manual:'手動模擬平倉',paused:'停止測試後平倉',interrupted:'輪詢中斷後平倉',
  market_error:'行情異常後平倉',missing_bars:'K 棒缺漏後平倉',long_breakout:'突破做多',short_breakout:'跌破做空',
  position_open:'已有持倉',daily_halt:'當日風控暫停',daily_entries:'達每日進場上限',loss_streak:'連虧暫停',
  cooldown:'平倉冷卻中',stale_signal:'訊號已過期',entry_drift:'價格偏離訊號',stop_distance:'停損距離不符',invalid_target:'停利價格無效'};
const states={idle:'尚未開始',starting:'讀取行情並建立起始點',waiting_signal:'運行中，等待新收盤突破訊號',
  position_open:'自動模擬持倉中',daily_halt:'當日風控暫停新進場',paused:'已停止新進場',closing:'等待有效行情模擬平倉',
  interrupted:'輪詢中斷，已停止新進場',market_error:'行情讀取失敗，已停止新進場',missing_bars:'K 棒缺漏，已停止新進場',closed:'已結束'};
let runner,result=null,replaying=false;
function rows(id,values){const root=$(id);root.replaceChildren();for(const valuesRow of values){const tr=document.createElement('tr');for(const v of valuesRow){const td=document.createElement('td');td.textContent=String(v);tr.append(td);}root.append(tr);}}
function render(s){
  $('strategy-state').textContent=(states[s.status]||s.status)+(s.busy?' · 正在讀取行情':'');
  $('strategy-start').disabled=s.active||s.busy||document.hidden;
  $('strategy-pause').disabled=!s.active;
  $('strategy-close').disabled=!s.account.position||s.busy;
  $('strategy-new').disabled=s.active||s.busy||Boolean(s.account.position);
  $('strategy-symbol').disabled=s.active||s.busy||Boolean(s.account.position);
  $('strategy-price').textContent=s.lastPrice===null?'尚未收到行情':number(s.lastPrice,6)+' USDT · 行情時間 '+time(s.lastQuoteTs);
  $('strategy-equity').textContent=number(s.summary.equity);$('strategy-pnl').textContent=number(s.summary.pnl);
  $('strategy-count').textContent=String(s.summary.count);$('strategy-win').textContent=s.summary.winRate===null?'—':number(s.summary.winRate*100,1)+'%';
  $('strategy-daily').textContent='台灣日期 '+(s.account.day??'—')+' · 今日進場 '+s.account.dailyEntries+'/6 · 連虧 '+s.account.lossStreak+'/3'+(s.account.dailyHalt?' · 當日暫停':'');
  const p=s.account.position;
  $('strategy-position').textContent=p?`${p.symbol} ${p.side===1?'做多':'做空'} · 模擬數量 ${number(p.qty,8)} 基礎幣單位 · 進場 ${number(p.entry,6)} · 停損 ${number(p.stop,6)} · 停利 ${number(p.target,6)} · 預估停損含費 ${number(p.estimatedStopLoss)} USDT`:'目前沒有自動模擬持倉';
  $('strategy-pending-close').textContent=!s.active&&p?'已停止新進場；既有模擬持倉會在下一次有效行情讀取後平倉。':'';
  rows('strategy-trades',s.account.closed.slice(-30).reverse().map(t=>[time(t.openedAt),t.side===1?'做多':'做空',number(t.entry,6),number(t.exit,6),number(t.pnl),reasons[t.reason]||t.reason]));
  rows('strategy-events',s.account.events.slice(-10).reverse().map(e=>[time(e.ts),e.type==='entry'?'進場':e.type==='exit'?'平倉':'略過',reasons[e.reason]||e.reason]));
  $('strategy-export').disabled=!s.account.closed.length;
}
function fresh(){runner?.dispose();runner=createPaperRunner({symbol:$('strategy-symbol').value,market:client,onChange:render});render(runner.getState());}
$('strategy-start').addEventListener('click',()=>runner.start());
$('strategy-pause').addEventListener('click',()=>runner.pause());
$('strategy-close').addEventListener('click',()=>runner.flatten());
$('strategy-new').addEventListener('click',fresh);
$('strategy-symbol').addEventListener('change',fresh);
function exportTrades(trades,name){
  if(!trades.length)return;
  const rows=[['策略版本','幣種','進場時間UTC','平倉時間UTC','方向','數量(基礎幣)','進場','停損','停利','出場','費用','淨損益USDT','原因'],
    ...trades.map(t=>[RULES.version,t.symbol,new Date(t.openedAt).toISOString(),new Date(t.closedAt).toISOString(),t.side===1?'LONG':'SHORT',t.qty,t.entry,t.stop,t.target,t.exit,t.fees,t.pnl,t.reason])];
  const csv='\uFEFF'+rows.map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');
  const u=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
}
$('strategy-export').addEventListener('click',()=>exportTrades(runner.getState().account.closed,'sinka-snr-paper.csv'));
$('strategy-backtest-export').addEventListener('click',()=>{if(result)exportTrades(result.account.closed,'sinka-snr-strategy-backtest.csv');});
$('strategy-backtest').addEventListener('click',async()=>{
  if(replaying)return;replaying=true;$('strategy-backtest').disabled=true;$('strategy-backtest-export').disabled=true;
  const symbol=$('strategy-symbol').value,pages=Number($('strategy-history-pages').value);
  $('strategy-backtest-state').textContent='正在讀取已收盤歷史 K 棒並驗證連續性…';
  const controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),45000);
  try{
    const bars=await client.history(symbol,{signal:controller.signal,pages});result=backtest(bars,symbol);
    $('strategy-backtest-state').textContent=`${RULES.version} · ${symbol} · ${bars.length} 根已收盤 5m · ${time(result.first)} ～ ${time(result.last)}（台灣）`;
    $('strategy-bt-pnl').textContent=number(result.pnl);$('strategy-bt-count').textContent=String(result.count);
    $('strategy-bt-win').textContent=result.winRate===null?'—':number(result.winRate*100,1)+'%';
    $('strategy-bt-dd').textContent=number(result.maxDrawdown*100,2)+'%';
    $('strategy-bt-pf').textContent=result.profitFactor===null?'—':number(result.profitFactor);
    rows('strategy-bt-trades',result.account.closed.slice(-30).reverse().map(t=>[time(t.openedAt),t.side===1?'做多':'做空',number(t.entry,6),number(t.exit,6),number(t.pnl),reasons[t.reason]||t.reason]));
    $('strategy-backtest-export').disabled=!result.count;
  }catch{result=null;for(const id of ['strategy-bt-pnl','strategy-bt-count','strategy-bt-win','strategy-bt-dd','strategy-bt-pf'])$(id).textContent='—';rows('strategy-bt-trades',[]);$('strategy-backtest-state').textContent='回測未完成：行情讀取逾時、缺少連續 K 棒或資料無效。請稍後重試。';}
  finally{clearTimeout(deadline);replaying=false;$('strategy-backtest').disabled=false;}
});
document.addEventListener('visibilitychange',()=>{if(document.hidden)runner.pause();else render(runner.getState());});
window.addEventListener('pagehide',()=>runner.dispose());
window.addEventListener('pageshow',event=>{if(event.persisted)fresh();});
fresh();
