"""Read public market signals from the existing JS engine, with no credentials."""
import asyncio
import json
import os
from pathlib import Path

from okx_risk import SYMBOLS
from okx_strategy_bridge import timestamp


class DemoStrategySource:
    def __init__(self):
        self.script=Path(__file__).resolve().parents[1]/'strategy'/'demo-signal.mjs'

    async def __call__(self,symbol,started_at):
        if symbol not in SYMBOLS: raise ValueError('Allowlisted strategy symbol required')
        timestamp(started_at)
        process=None
        try:
            # Do not inherit OKX credentials, proxy settings or NODE_OPTIONS.
            process=await asyncio.create_subprocess_exec('node',str(self.script),symbol,str(started_at),
                env={'PATH':os.environ.get('PATH',os.defpath)},
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            async with asyncio.timeout(7):
                output,_=await process.communicate()
            if process.returncode!=0 or len(output)>65536: raise ValueError('Public source unavailable')
            payload=json.loads(output)
            if not isinstance(payload,dict) or set(payload)!={'source'}:
                raise ValueError('Invalid source envelope')
            source=payload['source']
            if source is not None and (not isinstance(source,dict) or set(source)!={'strategy_version','signal','quote'}):
                raise ValueError('Invalid strategy source')
            return source
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ValueError('Public strategy source unavailable') from None
        finally:
            if process is not None and process.returncode is None:
                try: process.kill()
                except ProcessLookupError: pass
                await process.wait()
