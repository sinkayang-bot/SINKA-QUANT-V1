import {RULES,SYMBOLS,validateBars} from './snr-engine.mjs?v=strategy-1';
const ORIGIN='https://www.okx.com';
function symbolAllowed(symbol){if(!SYMBOLS.includes(symbol))throw Error('Unknown symbol');}
export function parseCandles(data){
  if(!Array.isArray(data)||data.length>300)throw Error('Invalid candle response');
  const bars=data.filter(r=>Array.isArray(r)&&r[8]==='1').map(r=>({ts:Number(r[0]),o:Number(r[1]),h:Number(r[2]),l:Number(r[3]),c:Number(r[4])})).sort((a,b)=>a.ts-b.ts);
  validateBars(bars);return bars;
}
async function get(path,params,signal,fetcher){
  const u=new URL(path,ORIGIN);for(const [k,v]of Object.entries(params))u.searchParams.set(k,v);
  const r=await fetcher(u.href,{method:'GET',credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer',signal});
  if(!r.ok)throw Error('Market HTTP error');const body=await r.json();
  if(body.code!=='0'||!Array.isArray(body.data))throw Error('Market API error');return body.data;
}
export function createMarketClient(fetcher=fetch){
  return {
    async snapshot(symbol,signal){
      symbolAllowed(symbol);
      const [candles,ticker]=await Promise.all([
        get('/api/v5/market/candles',{instId:symbol,bar:'5m',limit:'100'},signal,fetcher),
        get('/api/v5/market/ticker',{instId:symbol},signal,fetcher)]);
      const q=ticker[0];if(ticker.length!==1||q?.instId!==symbol)throw Error('Invalid ticker');
      return {bars:parseCandles(candles),price:Number(q.last),quoteTs:Number(q.ts)};
    },
    async history(symbol,{signal,pages=12}={}){
      symbolAllowed(symbol);const all=new Map();let after='';
      for(let page=0;page<Math.min(30,pages);page++){
        const data=await get('/api/v5/market/history-candles',{instId:symbol,bar:'5m',limit:'100',...(after?{after}:{})},signal,fetcher);
        if(!data.length)break;
        for(const b of parseCandles(data)){
          const old=all.get(b.ts);if(old&&JSON.stringify(old)!==JSON.stringify(b))throw Error('Conflicting candles');all.set(b.ts,b);
        }
        const oldest=Math.min(...data.map(r=>Number(r[0])));if(!Number.isSafeInteger(oldest)||after&&oldest>=Number(after))throw Error('History cursor stalled');after=String(oldest);
      }
      const bars=[...all.values()].sort((a,b)=>a.ts-b.ts);validateBars(bars);return bars;
    },
  };
}
export function validateSnapshot(snapshot,now){
  const {bars,price,quoteTs}=snapshot;validateBars(bars);
  if(bars.length<RULES.warmup||!Number.isFinite(price)||price<=0||!Number.isSafeInteger(quoteTs)||
    now-quoteTs>RULES.quoteAgeMs||quoteTs>now+2000)throw Error('Stale quote');
  const end=bars.at(-1).ts+RULES.barMs;
  if(end>now||now-end>RULES.barMs+20000)throw Error('Stale candles');
  return snapshot;
}
