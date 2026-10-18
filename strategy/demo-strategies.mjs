// Demo research variants. Closed candles only; no account, credentials or broker.
import {RULES,analyze,validateBars} from './snr-engine.mjs';
export const VERSIONS=Object.freeze({original:RULES.version,fast:'SNR-5M-FAST-1',retest:'SNR-5M-RETEST-1'});
const positive=x=>Number.isFinite(x)&&x>0;
const ema=bars=>bars.slice(1).reduce((e,b)=>e+(b.c-e)*2/51,bars[0].c);
function context(bars){
  const prior=bars.slice(-81,-1),trigger=bars.at(-1),zone=prior.slice(-20);
  const ranges=prior.slice(-14).map((b,j)=>{const p=prior[prior.length-14+j-1];return Math.max(b.h-b.l,Math.abs(b.h-p.c),Math.abs(b.l-p.c));});
  const atr=ranges.reduce((a,b)=>a+b,0)/14,oldEMA=ema(prior),trend=oldEMA+(trigger.c-oldEMA)*2/51;
  return {trigger,atr,trend,oldEMA,support:Math.min(...zone.map(b=>b.l)),resistance:Math.max(...zone.map(b=>b.h))};
}
export function analyzeVariant(bars,variant='original'){
  if(!Object.hasOwn(VERSIONS,variant))throw Error('Unknown demo variant');
  if(variant==='original')return analyze(bars);
  validateBars(bars);if(bars.length<81)return {reason:'warmup'};
  const {trigger:t,atr,trend,oldEMA,support,resistance}=context(bars);
  const trendOkay=side=>side*(t.c-t.o)>0&&side*(t.c-trend)>0&&side*(trend-oldEMA)>0;
  let side=0,level;
  if(variant==='fast'){
    if(t.c>resistance+.10*atr){side=1;level=resistance;}
    else if(t.c<support-.10*atr){side=-1;level=support;}
  }else{
    // Reclaim a FIRST breakout from one of the preceding three closed bars.
    // The range is fixed before that breakout; no future bars set its level.
    for(let ago=1;ago<=3&&!side;ago++){
      const index=bars.length-1-ago,b=bars[index],zone=bars.slice(index-20,index);
      const hi=Math.max(...zone.map(x=>x.h)),lo=Math.min(...zone.map(x=>x.l));
      const previous=bars[index-1],between=bars.slice(index+1,-1);
      if(b.c>hi+.10*atr&&previous.c<=hi&&between.every(x=>x.c>=hi-.20*atr)
          &&t.l<=hi+.20*atr&&t.l>=hi-atr&&t.c>hi+.05*atr){side=1;level=hi;}
      else if(b.c<lo-.10*atr&&previous.c>=lo&&between.every(x=>x.c<=lo+.20*atr)
          &&t.h>=lo-.20*atr&&t.h<=lo+atr&&t.c<lo-.05*atr){side=-1;level=lo;}
    }
  }
  const common={support,resistance,atr,ema:trend};
  if(!side||!positive(atr))return {reason:'no_signal',diagnostic:'breakout_not_met',...common};
  if(!trendOkay(side))return {reason:'no_signal',diagnostic:'trend_not_met',...common};
  const stop=side===1?Math.min(level-atr,t.l-.10*atr):Math.max(level+atr,t.h+.10*atr);
  if(!positive(stop))return {reason:'invalid_stop'};
  return {reason:'signal',id:String(t.ts),ts:t.ts,readyAt:t.ts+RULES.barMs,
    side,stop,close:t.c,...common};
}
export function selectDemoSignal(bars,profile='original'){
  if(!['original','expanded'].includes(profile))throw Error('Unknown demo strategy profile');
  // One candidate per market/bar, with deterministic priority. No mixed sizing.
  const variants=profile==='expanded'?['original','retest','fast']:['original'];
  let fallback;
  for(const variant of variants){
    const signal=analyzeVariant(bars,variant),value={signal,strategy_version:VERSIONS[variant]};
    fallback=value;if(signal.reason==='signal')return value;
  }
  return fallback;
}
