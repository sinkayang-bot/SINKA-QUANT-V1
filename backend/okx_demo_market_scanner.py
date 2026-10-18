"""Bounded 20-market public scanner; no credentials, account state or orders."""
import asyncio
import json
import os
from pathlib import Path
from okx_risk import SYMBOLS
from okx_strategy_bridge import timestamp

SCAN_FIELDS=frozenset({'universe','eligible','checked','no_signal','expired','prestart','fetch_failed','signals'})
REASON_FIELDS=frozenset({'breakout_not_met','trend_not_met','warmup','invalid_stop'})

class DemoMarketScanner:
    def __init__(self,*,profile='original'):
        if profile not in {'original','expanded'}: raise ValueError('Unknown strategy profile')
        self.profile=profile
        self.script=Path(__file__).resolve().parents[1]/'strategy'/'demo-market-scan.mjs'
        self.last_scan={}
        self.excluded=set()

    async def __call__(self,symbol,started_at):
        if symbol not in SYMBOLS: raise ValueError('Unknown market')
        timestamp(started_at)
        self.last_scan={}
        process=None
        try:
            process=await asyncio.create_subprocess_exec('node',str(self.script),str(started_at),*(['--expanded'] if self.profile=='expanded' else []),
                env={'PATH':os.environ.get('PATH',os.defpath)},
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            async with asyncio.timeout(10): output,_=await process.communicate()
            if process.returncode!=0 or len(output)>65536: raise ValueError()
            payload=json.loads(output)
            if not isinstance(payload,dict) or set(payload)!={'sources','scan'}: raise ValueError()
            scan=payload['scan']; sources=payload['sources']
            if (not isinstance(scan,dict) or set(scan) not in (SCAN_FIELDS,SCAN_FIELDS|REASON_FIELDS)
                    or any(type(v) is not int or not 0<=v<=20 for v in scan.values())
                    or scan['universe']!=20 or not isinstance(sources,list) or len(sources)>20): raise ValueError()
            if (scan['signals']!=len(sources) or scan['checked']+scan['fetch_failed']!=scan['eligible']
                    or sum(scan[k] for k in ('no_signal','expired','prestart','signals'))!=scan['checked']):
                raise ValueError()
            if REASON_FIELDS<=scan.keys() and sum(scan[k] for k in REASON_FIELDS)!=scan['no_signal']:
                raise ValueError('Rejected-market breakdown mismatch')
            seen=set()
            for source in sources:
                if not isinstance(source,dict) or set(source)!={'strategy_version','signal','quote'}: raise ValueError()
                quote=source['quote']
                if not isinstance(quote,dict) or quote.get('symbol') not in SYMBOLS: raise ValueError()
                market=quote['symbol']
                if market in seen: raise ValueError()
                seen.add(market)
            self.last_scan=dict(scan,excluded=len(self.excluded))
            return next((source for source in sources if source['quote']['symbol'] not in self.excluded),None)
        except asyncio.CancelledError: raise
        except Exception: raise ValueError('Public market scanner unavailable') from None
        finally:
            if process is not None and process.returncode is None:
                try: process.kill()
                except ProcessLookupError: pass
                await process.wait()
