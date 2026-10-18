// Research paper execution only. Pure functions: no network, credentials or broker.
export const SYMBOLS=Object.freeze(['BTC-USDT-SWAP','ETH-USDT-SWAP','SOL-USDT-SWAP','XRP-USDT-SWAP','DOGE-USDT-SWAP']);
export const RULES=Object.freeze({version:'SNR-5M-1',barMs:300000,lookback:40,warmup:81,atrPeriod:14,emaPeriod:50,
  breakoutATR:.25,rewardRisk:2,risk:.005,maxNotional:3,fee:.0005,slip:.0002,
  minStopFraction:.002,maxStopFraction:.03,maxEntryDriftATR:.5,maxHoldingBars:12,
  dailyLoss:.02,maxDailyEntries:6,maxConsecutiveLosses:3,cooldownMs:900000,signalAgeMs:20000,quoteAgeMs:15000});
const finitePositive=x=>typeof x==='number'&&Number.isFinite(x)&&x>0;
export function validBar(b){return b&&Number.isSafeInteger(b.ts)&&b.ts>=0&&b.ts%RULES.barMs===0&&
  [b.o,b.h,b.l,b.c].every(finitePositive)&&b.l<=Math.min(b.o,b.c)&&b.h>=Math.max(b.o,b.c)&&b.l<=b.h;}
export function validateBars(bars){
  if(!Array.isArray(bars)||bars.length>50000||!bars.every(validBar))throw Error('Invalid candles');
  for(let i=1;i<bars.length;i++)if(bars[i].ts-bars[i-1].ts!==RULES.barMs)throw Error('Non-contiguous candles');
  return bars;
}
const ema=values=>values.slice(1).reduce((e,v)=>e+(v-e)*2/(RULES.emaPeriod+1),values[0]);
export function analyze(bars){
  validateBars(bars);
  if(bars.length<RULES.warmup)return {reason:'warmup'};
  // A fixed window makes live polling and a long historical prefix identical.
  const window=bars.slice(-RULES.warmup),trigger=window.at(-1),prior=window.slice(0,-1);
  const zone=prior.slice(-RULES.lookback),resistance=Math.max(...zone.map(b=>b.h)),support=Math.min(...zone.map(b=>b.l));
  const ranges=prior.slice(-RULES.atrPeriod).map((b,j)=>{const prev=prior[prior.length-RULES.atrPeriod+j-1];return Math.max(b.h-b.l,Math.abs(b.h-prev.c),Math.abs(b.l-prev.c));});
  const atr=ranges.reduce((a,b)=>a+b,0)/ranges.length;
  const oldEMA=ema(prior.map(b=>b.c)),trend=oldEMA+(trigger.c-oldEMA)*2/(RULES.emaPeriod+1);
  let side=0;
  if(trigger.c>resistance+RULES.breakoutATR*atr&&trigger.c>trigger.o&&trigger.c>trend&&trend>oldEMA)side=1;
  if(trigger.c<support-RULES.breakoutATR*atr&&trigger.c<trigger.o&&trigger.c<trend&&trend<oldEMA)side=-1;
  if(!side||!finitePositive(atr))return {reason:'no_signal',support,resistance,atr,ema:trend};
  const stop=side===1?resistance-atr:support+atr;
  if(!finitePositive(stop))return {reason:'invalid_stop'};
  return {reason:'signal',id:String(trigger.ts),side,stop,atr,close:trigger.c,ts:trigger.ts,
    readyAt:trigger.ts+RULES.barMs,support,resistance,ema:trend};
}
export function createAccount(symbol='BTC-USDT-SWAP',initial=10000){
  if(!SYMBOLS.includes(symbol)||!finitePositive(initial))throw Error('Invalid paper account');
  return {version:RULES.version,mode:'local_paper',symbol,initial,cash:initial,position:null,closed:[],events:[],
    day:null,dayStart:initial,dailyEntries:0,lossStreak:0,dailyHalt:false,cooldownUntil:0,lastSignal:null};
}
const dayKey=ts=>new Date(ts+8*3600000).toISOString().slice(0,10);
export function markEquity(a,price){const p=a.position;return a.cash+(p?p.qty*p.side*(price-p.entry)-p.qty*price*RULES.fee:0);}
function day(a,ts,price){
  const key=dayKey(ts);if(key!==a.day){a.day=key;a.dayStart=markEquity(a,price);a.dailyEntries=0;a.lossStreak=0;a.dailyHalt=false;}
}
function log(a,ts,type,reason){a.events.push({ts,type,reason});if(a.events.length>200)a.events.shift();}
export function canEnter(a,ts,price){
  day(a,ts,price);
  if(a.position)return 'position_open';
  if(a.dailyHalt)return 'daily_halt';
  if(a.dailyEntries>=RULES.maxDailyEntries)return 'daily_entries';
  if(a.lossStreak>=RULES.maxConsecutiveLosses)return 'loss_streak';
  if(ts<a.cooldownUntil)return 'cooldown';
  if(!finitePositive(a.cash))return 'no_equity';
  return null;
}
export function enter(a,signal,raw,ts,{historical=false}={}){
  if(!signal||signal.reason!=='signal'||![1,-1].includes(signal.side)||!finitePositive(raw)||
    !finitePositive(signal.stop)||!finitePositive(signal.atr)||!finitePositive(signal.close)||!Number.isSafeInteger(ts)||
    !Number.isSafeInteger(signal.readyAt)||typeof signal.id!=='string')return 'invalid_signal';
  if(signal.id===a.lastSignal)return 'duplicate_signal';
  a.lastSignal=signal.id; // Consume rejected candidates too; never chase an old breakout.
  let reason=canEnter(a,ts,raw);
  if(!reason&&(ts<signal.readyAt||(!historical&&ts-signal.readyAt>RULES.signalAgeMs)))reason='stale_signal';
  if(!reason&&Math.abs(raw-signal.close)>RULES.maxEntryDriftATR*signal.atr)reason='entry_drift';
  const entry=raw*(1+signal.side*RULES.slip),distance=signal.side*(entry-signal.stop),fraction=distance/entry;
  if(!reason&&(!(fraction>=RULES.minStopFraction)||fraction>RULES.maxStopFraction))reason='stop_distance';
  const target=entry+signal.side*RULES.rewardRisk*distance;
  if(!reason&&!finitePositive(target))reason='invalid_target';
  if(reason){log(a,ts,'skip',reason);return reason;}
  // Include adverse stop slippage and entry/exit fees in the 0.5% budget.
  const stopped=signal.stop*(1-signal.side*RULES.slip);
  const cost=signal.side*(entry-stopped)+RULES.fee*(entry+stopped);
  const budget=a.cash*RULES.risk,qty=Math.min(budget/cost,a.cash*RULES.maxNotional/entry);
  const entryFee=qty*entry*RULES.fee;
  if(!finitePositive(qty)||entryFee>=a.cash)return 'invalid_size';
  a.cash-=entryFee;a.dailyEntries++;
  a.position={symbol:a.symbol,side:signal.side,entry,stop:signal.stop,target,qty,entryFee,openedAt:ts,
    expiresAt:ts+RULES.maxHoldingBars*RULES.barMs,signalId:signal.id,riskBudget:budget,estimatedStopLoss:qty*cost};
  log(a,ts,'entry',signal.side===1?'long_breakout':'short_breakout');return null;
}
export function exit(a,raw,ts,reason){
  if(!a.position||!finitePositive(raw)||!Number.isSafeInteger(ts))return false;
  day(a,ts,raw);const p=a.position,price=raw*(1-p.side*RULES.slip),exitFee=p.qty*price*RULES.fee;
  const gross=p.qty*p.side*(price-p.entry),pnl=gross-p.entryFee-exitFee;
  a.cash+=gross-exitFee;
  a.closed.push({...p,exit:price,closedAt:ts,reason,pnl,fees:p.entryFee+exitFee,balance:a.cash});
  a.position=null;a.lossStreak=pnl<0?a.lossStreak+1:0;a.cooldownUntil=ts+RULES.cooldownMs;
  if(a.cash<=a.dayStart*(1-RULES.dailyLoss)||a.lossStreak>=RULES.maxConsecutiveLosses)a.dailyHalt=true;
  log(a,ts,'exit',reason);return true;
}
export function onQuote(a,price,ts){
  if(!finitePositive(price)||!Number.isSafeInteger(ts))throw Error('Invalid quote');
  day(a,ts,price);const p=a.position;
  if(markEquity(a,price)<=a.dayStart*(1-RULES.dailyLoss)){
    if(p)exit(a,price,ts,'daily_loss');a.dailyHalt=true;return;
  }
  if(!p)return;
  if(p.side*(price-p.stop)<=0)exit(a,price,ts,'stop');
  else if(p.side*(price-p.target)>=0)exit(a,price,ts,'target');
  else if(ts>=p.expiresAt)exit(a,price,ts,'time_limit');
}
export function summary(a,price=a.position?.entry??a.cash){
  const wins=a.closed.filter(t=>t.pnl>0),losses=a.closed.filter(t=>t.pnl<0);
  const grossProfit=wins.reduce((s,t)=>s+t.pnl,0),grossLoss=-losses.reduce((s,t)=>s+t.pnl,0);
  return {balance:a.cash,equity:markEquity(a,price),pnl:a.cash-a.initial,count:a.closed.length,
    winRate:a.closed.length?wins.length/a.closed.length:null,profitFactor:grossLoss?grossProfit/grossLoss:null};
}
export function backtest(bars,symbol='BTC-USDT-SWAP'){
  validateBars(bars);if(bars.length<RULES.warmup+1)throw Error('Insufficient candles');
  const a=createAccount(symbol),curve=[];let pending=analyze(bars.slice(0,RULES.warmup)),peak=a.initial,maxDrawdown=0;
  function mark(price,ts){const equity=markEquity(a,price);peak=Math.max(peak,equity);maxDrawdown=Math.max(maxDrawdown,(peak-equity)/peak);curve.push({ts,equity});}
  for(let i=RULES.warmup;i<bars.length;i++){
    const b=bars[i];day(a,b.ts,b.o);
    if(pending)enter(a,pending,b.o,b.ts,{historical:true});
    const p=a.position;
    if(p){
      const stopHit=p.side===1?b.l<=p.stop:b.h>=p.stop,targetHit=p.side===1?b.h>=p.target:b.l<=p.target;
      // A gap beyond a stop executes at the worse opening price. Stops win ambiguous bars.
      if(stopHit)exit(a,p.side===1?Math.min(b.o,p.stop):Math.max(b.o,p.stop),b.ts,'stop');
      else if(targetHit)exit(a,p.target,b.ts,'target');
      else if(b.ts+RULES.barMs>=p.expiresAt)exit(a,b.c,b.ts+RULES.barMs,'time_limit');
      else onQuote(a,b.c,b.ts+RULES.barMs-1);
    }
    mark(b.c,b.ts+RULES.barMs-1);
    pending=analyze(bars.slice(Math.max(0,i-RULES.warmup+1),i+1));
  }
  const last=bars.at(-1);if(a.position){exit(a,last.c,last.ts+RULES.barMs-1,'end_of_data');mark(last.c,last.ts+RULES.barMs-1);}
  return {account:a,curve,maxDrawdown,...summary(a),first:bars[0].ts,last:last.ts};
}
