"""Fixed GET-only closed-entry/algo/child reader. Never submits or cancels.

A supported closed trade requires two stable detail passes bracketed by complete
verified account snapshots. The returned entry observation is persisted only by
the close auditor, atomically with the close ledger and current-owner check.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal
from okx_demo_close_audit import validate_lineage
from okx_demo_fill_audit import ident
from okx_demo_protection import _plan_body
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_transport import identifier
from okx_orders import parse_order
from okx_strategy_bridge import timestamp


class DemoCloseReadError(RuntimeError):
    pass


class DemoCloseReader:
    def __init__(self,snapshot_reader):
        if not isinstance(snapshot_reader,DemoSnapshotReader):
            raise ValueError('Verified fixed demo snapshot reader required')
        self.reader=snapshot_reader
        self.last_bundle=None

    async def _one(self,method,*args):
        received=timestamp(self.reader.clock())
        response=await method(*args)
        if (not isinstance(response,dict) or response.get('code')!='0'
                or not isinstance(response.get('data'),list) or len(response['data'])!=1
                or not isinstance(response['data'][0],dict)):
            raise ValueError('Single successful detail result required')
        return dict(complete=True,received_at_ms=received,rows=response['data'])

    async def _details(self,body,snapshot,expected_id):
        transport=self.reader.transport
        entry=await self._one(transport.lookup_entry,body['instId'],body['clOrdId'])
        raw=entry['rows'][0]; parsed=parse_order(raw)
        ident(parsed.order_id)
        if (parsed.client_order_id!=body['clOrdId'] or parsed.instrument!=body['instId']
                or (expected_id is not None and parsed.order_id!=expected_id)
                or parsed.state!='filled' or parsed.filled!=parsed.requested
                or any(raw.get(k)!=body[k] for k in ('side','posSide','tdMode','ordType'))):
            raise ValueError('Entry identity/state mismatch')
        if parsed.requested!=Decimal(body['sz']): raise ValueError('Entry quantity mismatch')
        attached=body['attachAlgoOrds'][0]['attachAlgoClOrdId']
        algo=await self._one(transport.lookup_algo,attached)
        detail=algo['rows'][0]
        children=detail.get('ordIdList')
        if (detail.get('algoClOrdId')!=attached or detail.get('instId')!=body['instId']
                or not isinstance(children,list) or len(children)!=1 or detail.get('subAlgoIdList',[])!=[]):
            raise ValueError('Unresolved or split protective child')
        child_id=ident(children[0])
        child=await self._one(transport.lookup_child,body['instId'],child_id)
        evidence=dict(demo_verified=True,account_scope=self.reader.scope,entry=entry,algo=algo,child=child)
        validate_lineage(body,parsed,evidence,snapshot,timestamp(self.reader.clock()),self.reader.scope)
        return evidence

    async def collect(self,plan,*,expected_entry_order_id=None):
        self.last_bundle=None
        try:
            body,_=_plan_body(deepcopy(plan)); identifier(body['clOrdId'])
            if body['instId']!=self.reader.symbol: raise ValueError('Reader/plan instrument mismatch')
            if expected_entry_order_id is not None: ident(expected_entry_order_id)
            start=timestamp(self.reader.clock())
            async with asyncio.timeout(5):
                before=await self.reader.collect()
                first=await self._details(body,before,expected_entry_order_id)
                second=await self._details(body,before,expected_entry_order_id)
                after=await self.reader.collect()
            now=timestamp(self.reader.clock())
            if not start<=before['received_at_ms']<=now or now-start>5000:
                raise ValueError('Close read bundle expired or clock regressed')
            def values(snapshot):
                return {k:v for k,v in snapshot.items() if k not in {'received_at_ms','scan_finished_at_ms'}}
            if values(before)!=values(after): raise ValueError('Account changed during detail reads')
            if any(first[k]['rows']!=second[k]['rows'] for k in ('entry','algo','child')):
                raise ValueError('Detail results changed between passes')
            snapshot=deepcopy(before); snapshot['scan_finished_at_ms']=now
            bundle=dict(snapshot=snapshot,close=first)
            self.last_bundle=deepcopy(bundle)
            return bundle
        except asyncio.CancelledError:
            self.reader.last_snapshot=None
            raise
        except Exception:
            self.reader.last_snapshot=None
            raise DemoCloseReadError('Demo close details unavailable or inconsistent') from None
