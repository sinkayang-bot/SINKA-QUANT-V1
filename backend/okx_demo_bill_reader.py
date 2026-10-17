"""GET-only bill adapter with verified account snapshots around two bill scans.

Default-off demo transport only. No order/cancel calls or trading authority.
A stable bounded window is evidence as of its cutoff, not future completeness.
"""
import asyncio
from copy import deepcopy
from okx_demo_reads import bill_route
from okx_demo_settlement import bill, scan
from okx_demo_snapshot import DemoSnapshotReader
from okx_strategy_bridge import timestamp


class DemoBillReadError(RuntimeError):
    pass


class DemoBillReader:
    def __init__(self, snapshot_reader):
        if not isinstance(snapshot_reader,DemoSnapshotReader):
            raise ValueError('Verified demo snapshot reader required')
        self.reader=snapshot_reader
        self.last_bundle=None

    async def _pages(self, begin, through):
        pages=[]; seen=set(); cursor=None
        for _ in range(6):
            response=await self.reader.transport.read_bills(begin,through,after=cursor)
            if (response.get('code')!='0' or not isinstance(response.get('data'),list)
                    or len(response['data'])>100):
                raise ValueError('Failed bill page')
            pages.append(response)
            for raw in response['data']:
                row=bill(raw); identity=int(row['billId'])
                if identity in seen or (cursor is not None and identity>=int(cursor)):
                    raise ValueError('Bill cursor did not advance')
                if not begin<=int(row['ts'])<=through:
                    raise ValueError('Bill outside interval')
                seen.add(identity); cursor=row['billId']
                if len(seen)>500: raise ValueError('Bill scan exceeds bound')
            if len(response['data'])<100:
                scan(pages,begin,through)
                return pages
        raise ValueError('Terminal bill page missing')

    async def collect(self, *, begin_ms):
        self.last_bundle=None
        try:
            start=timestamp(self.reader.clock())
            bill_route(begin_ms,start)
            if start-begin_ms>604800000:
                raise ValueError('Bill baseline outside recent endpoint retention')
            async with asyncio.timeout(5):
                # Account UID/config is verified before bill or amount reads.
                before=await self.reader.collect()
                through=before['received_at_ms']
                first=await self._pages(begin_ms,through)
                second=await self._pages(begin_ms,through)
                after=await self.reader.collect()
            now=timestamp(self.reader.clock())
            if not start<=through<=now or now-start>5000:
                raise ValueError('Bill bundle expired or clock regressed')
            def amounts(snapshot):
                return {k:v for k,v in snapshot.items() if k not in {'received_at_ms','scan_finished_at_ms'}}
            if amounts(before)!=amounts(after) or before.get('cash_balance_usdt') is None:
                raise ValueError('Account changed during bill scan or cash unavailable')
            if scan(first,begin_ms,through)!=scan(second,begin_ms,through):
                raise ValueError('Bill passes differ')
            snapshot=deepcopy(before)
            snapshot['scan_finished_at_ms']=now
            evidence=dict(demo_verified=True,account_scope=self.reader.scope,begin_ms=begin_ms,
                through_ms=through,received_at_ms=through,
                query=dict(ccy='USDT',begin=str(begin_ms),end=str(through),limit='100'),passes=[first,second])
            bundle=dict(snapshot=snapshot,bills=evidence)
            self.last_bundle=deepcopy(bundle)
            return bundle
        except asyncio.CancelledError:
            self.reader.last_snapshot=None
            raise
        except Exception:
            self.reader.last_snapshot=None
            raise DemoBillReadError('Demo bill window unavailable or inconsistent') from None
