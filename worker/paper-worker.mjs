#!/usr/bin/env node
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createMarketClient} from '../strategy/public-market.mjs';
import {createPaperRunner} from '../strategy/paper-runner.mjs';
import {SYMBOLS,RULES} from '../strategy/snr-engine.mjs';
import {openPaperStore} from './paper-store.mjs';
export function options(args){
  const config={symbol:'BTC-USDT-SWAP',db:null,maxSeconds:null,once:false};
  for(let i=0;i<args.length;i++){
    const arg=args[i];if(arg==='--once'){config.once=true;continue;}
    if(!['--db','--symbol','--max-seconds'].includes(arg)||!args[i+1]||args[i+1].startsWith('--'))throw Error('Invalid paper worker arguments');
    const value=args[++i];if(arg==='--db')config.db=resolve(value);if(arg==='--symbol')config.symbol=value;
    if(arg==='--max-seconds'){config.maxSeconds=Number(value);if(!Number.isInteger(config.maxSeconds)||config.maxSeconds<1||config.maxSeconds>86400)throw Error('Invalid bounded runtime');}
  }
  if(!config.db||!SYMBOLS.includes(config.symbol))throw Error('Specify --db and an allowed --symbol');return config;
}
export async function runWorker(config,{market=createMarketClient(),log=line=>process.stdout.write(JSON.stringify(line)+'\n'),
  now=Date.now,processSignals=true,setHeartbeat=setInterval,clearHeartbeat=clearInterval}={}){
  const store=openPaperStore(config.db,{symbol:config.symbol,now});let runner,heartbeat=null,limit=null,done=false;
  let lastStatus=null,resolveDone,heartbeatTicks=0;
  const completed=new Promise(resolve=>{resolveDone=resolve;});
  const stateLog=s=>({mode:'local_paper',strategy:RULES.version,status:s.status,active:s.active,
    positionOpen:Boolean(s.account.position),closedTrades:s.account.closed.length,revision:store.revision,
    at:new Date(now()).toISOString(),lastQuoteAt:s.lastQuoteTs===null?null:new Date(s.lastQuoteTs).toISOString(),
    quoteFresh:s.lastQuoteTs!==null&&now()-s.lastQuoteTs>=-2000&&now()-s.lastQuoteTs<=RULES.quoteAgeMs});
  function finish(code){
    if(done)return;done=true;
    if(heartbeat!==null)clearHeartbeat(heartbeat);if(limit!==null)clearTimeout(limit);
    if(processSignals){process.off('SIGTERM',onStop);process.off('SIGINT',onStop);}
    // Disposing checkpoints the still-open position; never fabricate a shutdown fill.
    runner?.dispose();store.close();resolveDone(code);
  }
  function onStop(){finish(0);}
  try{
    const initialAccount=store.load();
    runner=createPaperRunner({symbol:config.symbol,market,initialAccount,now,checkpoint:a=>store.save(a),onChange:s=>{
      if(s.status!==lastStatus){lastStatus=s.status;log(stateLog(s));}
      if(done)return;
      if(s.status==='storage_error'){finish(2);return;}
      if(!s.busy&&!s.active&&!s.account.position&&['market_error','interrupted','missing_bars','recovered'].includes(s.status))finish(s.status==='recovered'?0:2);
    }});
    if(processSignals){process.on('SIGTERM',onStop);process.on('SIGINT',onStop);}
    heartbeat=setHeartbeat(()=>{try{store.renew();if(++heartbeatTicks%6===0)log({...stateLog(runner.getState()),event:'heartbeat'});}catch{finish(2);}},5000);
    if(config.maxSeconds!==null)limit=setTimeout(()=>{finish(0);},config.maxSeconds*1000);
    await runner.start();
    if(config.once&&!done){finish(0);}
    return await completed;
  }catch(error){
    if(!done){done=true;if(heartbeat!==null)clearHeartbeat(heartbeat);if(limit!==null)clearTimeout(limit);if(processSignals){process.off('SIGTERM',onStop);process.off('SIGINT',onStop);}runner?.dispose();store.close();}
    throw error;
  }
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  try{process.exitCode=await runWorker(options(process.argv.slice(2)));}
  catch{process.stderr.write('Paper worker stopped: invalid configuration, busy lease, corrupt checkpoint or storage failure.\n');process.exitCode=2;}
}
