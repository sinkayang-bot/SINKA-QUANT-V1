"""Lease-gated entrypoint for the Firestore paper monitor.

Start only in a controlled paper-only environment.
"""
import asyncio
import logging
import os

from firestore_ledger import PaperLedger
from firestore_monitor import INTERVAL, check_once, write_heartbeat
from monitor_lease import MonitorLease

log = logging.getLogger("sinka.lease_worker")


async def run():
    owner = os.environ["SINKA_OWNER_UID"]
    ledger = PaperLedger(account_id=owner)
    lease = MonitorLease(ledger)
    delay = INTERVAL
    try:
        while True:
            try:
                if not await asyncio.to_thread(lease.acquire):
                    await asyncio.sleep(INTERVAL)
                    continue
                status = await check_once(ledger, lease=lease)
                if await asyncio.to_thread(lease.is_owner):
                    await asyncio.to_thread(write_heartbeat, ledger, status)
                delay = INTERVAL
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("paper monitor iteration failed")
                delay = min(60.0, max(INTERVAL, delay * 2))
            await asyncio.sleep(delay)
    finally:
        await asyncio.to_thread(lease.release)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
