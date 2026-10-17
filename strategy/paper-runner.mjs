import {RULES,createAccount,analyze,enter,onQuote,exit,summary} from './snr-engine.mjs?v=strategy-1';
import {restoreAccount} from './account-checkpoint.mjs?v=worker-1';
import {validateSnapshot} from './public-market.mjs?v=strategy-1';
// No broker interface exists. All fills are account arithmetic in this module.
export function createPaperRunner({symbol,market,onChange=()=>{},checkpoint=()=>{},initialAccount=null,now=Date.now,
  monotonicNow=()=>performance.now(),setTimer=setTimeout,clearTimer=clearTimeout}){
  const account=initialAccount===null?createAccount(symbol):restoreAccount(initialAccount,symbol);let active=false,busy=false,timer=null,request=null,generation=0;
  let status='idle',lastBar=null,lastPrice=null,lastQuoteTs=null,lastPoll=null,flattenReason=account.position?'recovery':null,disposed=false;
  if(flattenReason)status='recovery_pending';
  const state=()=>({active,busy,status,lastPrice,lastQuoteTs,lastBar,account:structuredClone(account),summary:summary(account,lastPrice??account.position?.entry)});
  const publish=()=>{
    try{checkpoint(structuredClone(account));}
    catch{active=false;busy=false;status='storage_error';disposed=true;generation++;request?.abort();if(timer!==null)clearTimer(timer);timer=null;}
    onChange(state());
  };
  function schedule(){if(timer!==null)clearTimer(timer);timer=null;if(!disposed&&(active||account.position))timer=setTimer(()=>{timer=null;poll();},5000);}
  function halt(reason='paused'){active=false;status=reason;if(account.position)flattenReason=reason;publish();schedule();}
  async function poll(){
    if(busy||disposed||(!active&&!account.position))return;
    busy=true;publish();if(disposed)return;const version=generation,controller=new AbortController();request=controller;
    let timeout;const deadline=new Promise((_,reject)=>{timeout=setTimer(()=>{controller.abort();reject(Error('Timeout'));},10000);});
    try{
      const snap=validateSnapshot(await Promise.race([market.snapshot(account.symbol,controller.signal),deadline]),now());
      if(version!==generation||disposed)return;
      const instant=now(),elapsed=monotonicNow();
      if(lastPoll!==null&&(elapsed-lastPoll>20000||elapsed<lastPoll)){
        active=false;status='interrupted';if(account.position&&!flattenReason)flattenReason='interrupted';
      }
      lastPoll=elapsed;lastPrice=snap.price;lastQuoteTs=snap.quoteTs;
      if(lastBar!==null&&snap.bars.at(-1).ts>lastBar+RULES.barMs){active=false;status='missing_bars';if(account.position&&!flattenReason)flattenReason='missing_bars';}
      if(lastQuoteTs>instant+2000)throw Error('Clock mismatch');
      if(flattenReason&&account.position){const recovered=flattenReason==='recovery';exit(account,snap.price,instant,flattenReason);flattenReason=null;if(recovered)status='recovered';}
      onQuote(account,snap.price,instant);
      const latest=snap.bars.at(-1).ts;
      if(lastBar!==null&&latest<lastBar)throw Error('Regressed candles');
      // Bootstrap establishes a watermark; never trade historical startup signals.
      if(active&&lastBar!==null&&latest>lastBar){
        const candidate=analyze(snap.bars);
        if(candidate.reason==='signal'&&snap.quoteTs>=candidate.readyAt)enter(account,candidate,snap.price,instant);
      }
      lastBar=latest;
      if(active)status=account.dailyHalt?'daily_halt':account.position?'position_open':'waiting_signal';
    }catch{
      if(version===generation&&!disposed){active=false;status='market_error';if(account.position&&!flattenReason)flattenReason='market_error';}
    }finally{
      clearTimer(timeout);if(request===controller)request=null;
      if(version===generation&&!disposed){busy=false;publish();schedule();}
    }
  }
  async function start(){
    if(disposed||active||busy)return;
    if(account.position&&flattenReason){await poll();return;}
    active=true;status='starting';lastBar=null;lastPoll=null;flattenReason=null;publish();if(!disposed)await poll();
  }
  function pause(){halt('paused');}
  async function flatten(){
    if(!account.position){pause();return;}active=false;flattenReason='manual';status='closing';publish();await poll();
  }
  function dispose(){disposed=true;active=false;generation++;request?.abort();if(timer!==null)clearTimer(timer);timer=null;busy=false;status='closed';publish();}
  return {start,pause,flatten,poll,dispose,getState:state};
}
