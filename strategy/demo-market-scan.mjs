// Public GETs only. Eligibility means liquidity screening, not price stability.
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {RULES,analyze} from './snr-engine.mjs';
import {parseCandles,validateSnapshot} from './public-market.mjs';
export const SCAN_SYMBOLS=Object.freeze(['BTC','ETH','SOL','XRP','DOGE','ADA','AVAX','LINK','DOT','LTC','BCH','TRX','TON','SUI','NEAR','APT','ATOM','UNI','ETC','FIL'].map(x=>x+'-USDT-SWAP'));
const SYMBOLS=SCAN_SYMBOLS;

export function eligibleMarkets(instruments,tickers,now) {
  if(!Array.isArray(instruments)||!Array.isArray(tickers)||instruments.length>5000||tickers.length>5000)throw Error('Invalid inventory');
  const eligible=[];
  for(const symbol of SYMBOLS){
    const ins=instruments.filter(x=>x?.instId===symbol),quotes=tickers.filter(x=>x?.instId===symbol);
    if(ins.length!==1||quotes.length!==1)continue;
    const i=ins[0],q=quotes[0],last=Number(q.last),bid=Number(q.bidPx),ask=Number(q.askPx);
    const volume=Number(q.volCcy24h)*last,listed=Number(i.listTime),ts=Number(q.ts);
    const high=Number(q.high24h),low=Number(q.low24h);
    if(i.instType!=='SWAP'||i.state!=='live'||i.ctType!=='linear'||i.settleCcy!=='USDT'||
       i.ctValCcy!==symbol.split('-')[0]||!Number.isSafeInteger(listed)||listed<=0||now-listed<90*86400000||
       !Number.isSafeInteger(ts)||ts>now||now-ts>RULES.quoteAgeMs||
       ![last,bid,ask,volume,high,low].every(x=>Number.isFinite(x)&&x>0)||ask<bid||
       volume<10000000||(ask-bid)/((ask+bid)/2)>.001||high<low||(high-low)/low>.20)continue;
    if(['ctVal','lotSz','minSz','tickSz','maxMktSz','maxStopSz'].some(k=>!Number.isFinite(Number(i[k]))||Number(i[k])<=0)||
       !Array.isArray(i.upcChg??[])||(i.upcChg??[]).length)continue;
    eligible.push({symbol,volume});
  }
  return eligible.sort((a,b)=>b.volume-a.volume||a.symbol.localeCompare(b.symbol));
}
async function inventory(path,params,fetcher,signal){
  const url=new URL(path,'https://www.okx.com');for(const [k,v] of Object.entries(params))url.searchParams.set(k,v);
  const r=await fetcher(url.href,{method:'GET',credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer',signal});
  if(!r.ok)throw Error('Public inventory unavailable');const body=await r.json();
  if(body.code!=='0'||!Array.isArray(body.data))throw Error('Public inventory invalid');return body.data;
}
export async function scanMarkets({startedAt,fetcher=fetch,clock=Date.now}){
  if(!Number.isSafeInteger(startedAt)||startedAt<0||startedAt>clock())throw Error('Invalid start');
  const signal=AbortSignal.timeout(8000);
  const [instruments,tickers]=await Promise.all([
    inventory('/api/v5/public/instruments',{instType:'SWAP'},fetcher,signal),
    inventory('/api/v5/market/tickers',{instType:'SWAP'},fetcher,signal)]);
  const markets=eligibleMarkets(instruments,tickers,clock()),sources=[];
  const scan={universe:SYMBOLS.length,eligible:markets.length,checked:0,no_signal:0,expired:0,prestart:0,fetch_failed:0,signals:0};
  let cursor=0;
  async function worker(){
    for(;;){
      const index=cursor++;if(index>=markets.length)return;
      const {symbol}=markets[index];
      try{
        const [candles,quotes]=await Promise.all([
          inventory('/api/v5/market/candles',{instId:symbol,bar:'5m',limit:'100'},fetcher,signal),
          inventory('/api/v5/market/ticker',{instId:symbol},fetcher,signal)]);
        if(quotes.length!==1||quotes[0]?.instId!==symbol)throw Error('Invalid ticker');
        const snapshot={bars:parseCandles(candles),price:Number(quotes[0].last),quoteTs:Number(quotes[0].ts)};
        const now=clock();validateSnapshot(snapshot,now);
        const result=analyze(snapshot.bars);scan.checked++;
        if(result.reason!=='signal'){scan.no_signal++;continue;}
        if(result.readyAt<=startedAt){scan.prestart++;continue;}
        if(now-result.readyAt>RULES.signalAgeMs||snapshot.quoteTs>now){scan.expired++;continue;}
        const source={strategy_version:RULES.version,signal:result,
          quote:{symbol,price:String(snapshot.price),ts:snapshot.quoteTs}};
        sources.push({source,index});
      }catch{scan.fetch_failed++;}
    }
  }
  await Promise.all(Array.from({length:Math.min(4,markets.length)},worker));
  const now=clock();
  const fresh=sources.sort((a,b)=>a.index-b.index).filter(({source})=>{
    const okay=now-source.signal.readyAt<=RULES.signalAgeMs&&now-source.quote.ts<=RULES.quoteAgeMs;
    if(!okay)scan.expired++;return okay;
  }).map(x=>x.source);
  scan.signals=fresh.length;
  return {sources:fresh,scan};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  try{
    if(process.argv.length!==3||!/^\d+$/.test(process.argv[2]))throw Error('Invalid arguments');
    process.stdout.write(JSON.stringify(await scanMarkets({startedAt:Number(process.argv[2])}))+'\n');
  }catch{process.stdout.write('{"error":"market_scan_unavailable"}\n');process.exitCode=2;}
}
