import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {analyze,RULES,SYMBOLS} from './snr-engine.mjs';
import {createMarketClient,validateSnapshot} from './public-market.mjs';

export function sourceFromSnapshot(snapshot,{symbol,startedAt,now}) {
  if(!SYMBOLS.includes(symbol)||!Number.isSafeInteger(startedAt)||startedAt<0||startedAt>now)throw Error('Invalid source configuration');
  validateSnapshot(snapshot,now);
  const signal=analyze(snapshot.bars);
  if(signal.reason!=='signal'||signal.readyAt<=startedAt||now-signal.readyAt>RULES.signalAgeMs||snapshot.quoteTs>now)return null;
  return {strategy_version:RULES.version,signal,quote:{symbol,price:String(snapshot.price),ts:snapshot.quoteTs}};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  try{
    const [symbol,started]=process.argv.slice(2);
    const testing=process.argv.length===5&&process.argv[4]==='--execution-test'&&symbol==='BTC-USDT-SWAP';
    if((process.argv.length!==4&&!testing)||!SYMBOLS.includes(symbol)||!/^\d+$/.test(started??''))throw Error('Invalid arguments');
    const snapshot=await createMarketClient().snapshot(symbol,AbortSignal.timeout(5000));
    let source;
    if(testing){
      const now=Date.now();validateSnapshot(snapshot,now);
      if(snapshot.quoteTs>now)throw Error('Future quote');
      source={strategy_version:'DEMO-EXECUTION-TEST-1',signal:null,quote:{symbol,price:String(snapshot.price),ts:snapshot.quoteTs}};
    }else source=sourceFromSnapshot(snapshot,{symbol,startedAt:Number(started),now:Date.now()});
    process.stdout.write(JSON.stringify({source})+'\n');
  }catch{
    process.stdout.write(JSON.stringify({error:'market_source_unavailable'})+'\n');process.exitCode=2;
  }
}
