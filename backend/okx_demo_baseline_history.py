"""GET-only recovery of exact missing pre-baseline fills, never new activity.

Recent fills expire after three days. Recover only identities already retained
in the immutable baseline, with two matching historical reads. Current fills,
full bills, cash, flatness and order checks are still independently required.
"""
import asyncio
from copy import deepcopy
from okx_demo_fill_audit import normalize_fill, key, encoded


async def recover_baseline_history(saved, snapshot, transport, now):
    present={key(normalize_fill(row,now)) for row in snapshot['recent_fills']}
    wanted={identity:row for identity,row in saved['baseline'].items() if identity not in present}
    if not wanted: return {}
    for identity,row in wanted.items():
        fill=normalize_fill(row,now)
        if key(fill)!=identity or int(fill['fillTime'])>saved['begin']:
            raise ValueError('Historical recovery requires original pre-baseline identities')

    async def scan():
        found={}; seen=set(); cursor=None
        for _ in range(6):
            response=await transport.account_read('fills_history',after=cursor)
            rows=response.get('data')
            if response.get('code')!='0' or not isinstance(rows,list) or len(rows)>100:
                raise ValueError('Historical fill page unavailable')
            for row in rows:
                fill=normalize_fill(row,now); bill=int(fill['billId'])
                if bill in seen or (cursor is not None and bill>=int(cursor)):
                    raise ValueError('Historical fill cursor did not advance')
                seen.add(bill); cursor=fill['billId']
                if len(seen)>500: raise ValueError('Historical fill scan exceeds bound')
                identity=key(fill)
                if identity in wanted:
                    if identity in found or encoded(fill)!=encoded(normalize_fill(wanted[identity],now)):
                        raise ValueError('Historical baseline fill changed')
                    found[identity]=fill
            if len(found)==len(wanted): return found
            if len(rows)<100: break
        raise ValueError('Original baseline fill missing from historical endpoint')

    async with asyncio.timeout(12):
        first=await scan()
        second=await scan()
    if first!=second: raise ValueError('Historical baseline evidence changed')
    return first


def with_baseline_history(snapshot,saved,history,now):
    """Merge only independently retrieved exact old identities into fresh proof.

    No row is synthesized from the local ledger. All returned history came from
    two demo GET scans; fresh current snapshots and bill cutoffs are unchanged.
    """
    result=deepcopy(snapshot)
    if not history:
        return result
    current={key(normalize_fill(row,now)):normalize_fill(row,now) for row in result['recent_fills']}
    for identity,row in history.items():
        fill=normalize_fill(row,now)
        if (identity not in saved['baseline'] or key(fill)!=identity
                or int(fill['fillTime'])>saved['begin']
                or encoded(fill)!=encoded(normalize_fill(saved['baseline'][identity],now))
                or (identity in current and current[identity]!=fill)):
            raise ValueError('Exact pre-baseline historical evidence required')
        if identity not in current:
            result['recent_fills'].append(deepcopy(fill)); current[identity]=fill
    if len(result['recent_fills'])>500: raise ValueError('Combined fill evidence exceeds bound')
    return result
