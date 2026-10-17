import {RULES,SYMBOLS} from './snr-engine.mjs?v=strategy-1';
const positive=v=>typeof v==='number'&&Number.isFinite(v)&&v>0;
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const timestamp=v=>Number.isSafeInteger(v)&&v>=0&&v<8640000000000000;
const near=(a,b)=>Math.abs(a-b)<=1e-8*Math.max(1,Math.abs(a),Math.abs(b));
const dayOf=ts=>new Date(ts+8*3600000).toISOString().slice(0,10);
const keys=(value,expected)=>value&&typeof value==='object'&&!Array.isArray(value)&&
  Object.keys(value).sort().join(',')===expected.split(',').sort().join(',');
const POSITION='symbol,side,entry,stop,target,qty,entryFee,openedAt,expiresAt,signalId,riskBudget,estimatedStopLoss';
const ACCOUNT='version,mode,symbol,initial,cash,position,closed,events,day,dayStart,dailyEntries,lossStreak,dailyHalt,cooldownUntil,lastSignal';
const REASONS=new Set(['stop','target','time_limit','daily_loss','end_of_data','manual','paused','interrupted','market_error','missing_bars','recovery']);
function position(p,symbol,cashBefore,closed=false){
  if(!keys(p,POSITION+(closed?',exit,closedAt,reason,pnl,fees,balance':''))||p.symbol!==symbol||![1,-1].includes(p.side)||
    !['entry','stop','target','qty','entryFee','riskBudget','estimatedStopLoss'].every(k=>positive(p[k]))||
    !timestamp(p.openedAt)||p.expiresAt!==p.openedAt+RULES.maxHoldingBars*RULES.barMs||
    typeof p.signalId!=='string'||p.signalId.length>32||
    !near(p.entryFee,p.qty*p.entry*RULES.fee)||!near(p.riskBudget,cashBefore*RULES.risk)||
    p.qty*p.entry>cashBefore*RULES.maxNotional+1e-6)throw Error('Invalid paper position checkpoint');
  const dist=p.side*(p.entry-p.stop),fraction=dist/p.entry,stopped=p.stop*(1-p.side*RULES.slip);
  const loss=p.qty*(p.side*(p.entry-stopped)+RULES.fee*(p.entry+stopped));
  if(fraction<RULES.minStopFraction-1e-12||fraction>RULES.maxStopFraction+1e-12||
    !near(p.target,p.entry+p.side*RULES.rewardRisk*dist)||!near(p.estimatedStopLoss,loss)||loss>p.riskBudget+1e-6)
    throw Error('Inconsistent paper risk checkpoint');
}
export function restoreAccount(input,symbol){
  if(!keys(input,ACCOUNT)||input.version!==RULES.version||input.mode!=='local_paper'||input.symbol!==symbol||
    !SYMBOLS.includes(symbol)||!positive(input.initial)||!finite(input.cash)||!finite(input.dayStart)||
    !Array.isArray(input.closed)||input.closed.length>100000||!Array.isArray(input.events)||input.events.length>200||
    !Number.isInteger(input.dailyEntries)||input.dailyEntries<0||input.dailyEntries>RULES.maxDailyEntries||
    !Number.isInteger(input.lossStreak)||input.lossStreak<0||input.lossStreak>RULES.maxConsecutiveLosses||
    typeof input.dailyHalt!=='boolean'||!timestamp(input.cooldownUntil)||
    !(input.day===null||typeof input.day==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(input.day))||
    !(input.lastSignal===null||typeof input.lastSignal==='string'&&/^\d{1,16}$/.test(input.lastSignal)))
    throw Error('Invalid paper account checkpoint');
  if(input.day===null&&(input.closed.length||input.position||input.dailyEntries||input.lossStreak||input.dailyHalt))throw Error('Missing paper risk day');
  if(input.day!==null){const parsed=Date.parse(input.day+'T00:00:00Z');if(!Number.isFinite(parsed)||new Date(parsed).toISOString().slice(0,10)!==input.day)throw Error('Invalid paper day');}
  let cash=input.initial,lastClosed=0,lossStreak=0;
  for(const t of input.closed){
    position(t,symbol,cash,true);
    if(!positive(t.exit)||!timestamp(t.closedAt)||t.closedAt<t.openedAt||t.openedAt<lastClosed||
      !finite(t.pnl)||!finite(t.fees)||!finite(t.balance)||!REASONS.has(t.reason))throw Error('Invalid paper fill checkpoint');
    const fees=t.entryFee+t.qty*t.exit*RULES.fee,pnl=t.qty*t.side*(t.exit-t.entry)-fees;
    cash+=pnl;
    if(!near(t.fees,fees)||!near(t.pnl,pnl)||!near(t.balance,cash))throw Error('Paper ledger does not reconcile');
    lastClosed=t.closedAt;
    if(dayOf(t.closedAt)===input.day)lossStreak=t.pnl<0?lossStreak+1:0;
  }
  if(input.position!==null){position(input.position,symbol,cash);if(input.position.openedAt<lastClosed)throw Error('Overlapping paper fills');cash-=input.position.entryFee;}
  if(!near(input.cash,cash))throw Error('Paper cash does not reconcile');
  const entries=input.closed.filter(t=>dayOf(t.openedAt)===input.day).length+
    (input.position&&dayOf(input.position.openedAt)===input.day?1:0);
  if(entries!==input.dailyEntries||lossStreak!==input.lossStreak||input.lossStreak>=RULES.maxConsecutiveLosses&&!input.dailyHalt)
    throw Error('Paper daily counters do not reconcile');
  for(const e of input.events)if(!keys(e,'ts,type,reason')||!timestamp(e.ts)||!['entry','exit','skip'].includes(e.type)||
    typeof e.reason!=='string'||!/^[a-z_]{1,48}$/.test(e.reason))throw Error('Invalid paper event checkpoint');
  return structuredClone(input);
}
