// Public historical research only. Does not load any broker or credential code.
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {RULES,SYMBOLS,validateBars,createAccount,enter,exit,markEquity,summary} from './snr-engine.mjs';
import {analyzeVariant,selectDemoSignal} from './demo-strategies.mjs';
import {createMarketClient} from './public-market.mjs';
export function replay(bars,symbol,variant){
  validateBars(bars);if(bars.length<82)throw Error('Insufficient historical candles');
  const analyze=prefix=>variant==='expanded'?selectDemoSignal(prefix,'expanded').signal:analyzeVariant(prefix,variant);
  const a=createAccount(symbol),first=80;let detected=0,peak=a.initial,drawdown=0;
  for(let i=first+1;i<bars.length;i++){
    const signal=analyze(bars.slice(0,i)),b=bars[i];
    if(signal.reason==='signal')detected++;
    // Match the configured demo policy: keep accounting, remove daily stops.
    a.dailyHalt=false;a.dailyEntries=0;a.lossStreak=0;a.cooldownUntil=0;
    enter(a,signal,b.o,b.ts,{historical:true});
    const p=a.position;
    if(p){
      const stopped=p.side===1?b.l<=p.stop:b.h>=p.stop,target=p.side===1?b.h>=p.target:b.l<=p.target;
      if(stopped)exit(a,p.side===1?Math.min(b.o,p.stop):Math.max(b.o,p.stop),b.ts,'stop');
      else if(target)exit(a,p.target,b.ts,'target');
      else if(b.ts+RULES.barMs>=p.expiresAt)exit(a,b.c,b.ts+RULES.barMs,'time_limit');
    }
    const equity=markEquity(a,b.c);peak=Math.max(peak,equity);drawdown=Math.max(drawdown,(peak-equity)/peak);
  }
  const last=bars.at(-1);if(a.position)exit(a,last.c,last.ts+RULES.barMs,'end_of_data');
  peak=Math.max(peak,a.cash);drawdown=Math.max(drawdown,(peak-a.cash)/peak);
  return {variant,signal_count:detected,...summary(a),max_drawdown:drawdown,
    first_ms:bars[first].ts,last_ms:last.ts+RULES.barMs,bars:bars.length,
    fees:a.closed.reduce((n,t)=>n+t.fees,0)};
}
export async function comparePublic({fetcher=fetch,pages=6,includeCandles=false}={}){
  if(!Number.isSafeInteger(pages)||pages<2||pages>12)throw Error('Bounded history required');
  const client=createMarketClient(fetcher),rows=[];
  for(const symbol of SYMBOLS){
    const bars=await client.history(symbol,{pages,signal:AbortSignal.timeout(30000)});
    rows.push({symbol,...(includeCandles?{candles:bars}:{}),results:['original','fast','retest','expanded'].map(v=>replay(bars,symbol,v))});
  }
  return {mode:'public_historical_research',fetched_at_ms:Date.now(),orders_sent:0,daily_limits_enabled:false,
    assumptions:'Closed 5m bars; next-open entry; 0.05% fee per side; 0.02% adverse slippage; stop first on ambiguous bars; fractional paper sizing without exchange tick/lot rounding. Per-market research, not a shared-account portfolio or profit forecast.',rows};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  try{if(process.argv.length!==3||process.argv[2]!=='--public')throw Error('Explicit public history flag required');
    process.stdout.write(JSON.stringify(await comparePublic())+'\n');
  }catch{process.stdout.write('{"mode":"public_historical_research","error":"history_comparison_unavailable","orders_sent":0}\n');process.exitCode=2;}
}
