"""Standalone read-only OKX diagnostic poller, deliberately disabled by default.

Run only in a separately managed worker service with Secret Manager-backed
read-only credentials. No order execution, no automatic trade authorization.
"""
import asyncio
import logging
import os

from google.cloud import firestore
from okx_firestore_sync import OKXSyncLedger
from okx_order_reader import OKXOrderReader
from okx_order_journal import OKXOrderJournal
from okx_sync import sync_once
from okx_sync_lease import OKXSyncLease

LOG = logging.getLogger("sinka.okx_readonly_worker")
INTERVAL_SECONDS = 15

async def poll_once(*, reader, ledger, lease, journal=None):
    """One iteration; return False when another worker owns the lease."""
    if not await asyncio.to_thread(lease.acquire):
        return False
    # Read existing journal records; no unknown exchange orders are auto-adopted.
    tracked = await asyncio.to_thread(journal.list_tracked) if journal else []
    async def persist_verified(order):
        if journal is None:
            return False
        return await asyncio.to_thread(journal.refresh, lease.worker_id, order)

    status = await sync_once(reader=reader, local_position=None,
                             tracked_orders=tracked,
                             persist_verified=persist_verified)
    stored = await asyncio.to_thread(ledger.write, lease.worker_id, status)
    if not stored:
        LOG.warning("OKX sync lease lost; diagnostic update rejected")
    return stored

async def run(*, reader, ledger, lease, journal=None, delay=INTERVAL_SECONDS):
    """Keep polling; failures are not treated as a successful sync."""
    try:
        while True:
            try:
                await poll_once(reader=reader, ledger=ledger, lease=lease,
                                journal=journal)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("OKX read-only sync iteration failed")
            await asyncio.sleep(delay)
    finally:
        try:
            await asyncio.to_thread(lease.release)
        except Exception:
            LOG.exception("Failed to release OKX sync lease")

def main():
    if os.getenv("SINKA_OKX_READONLY_MONITOR") != "true":
        raise RuntimeError("OKX read-only worker disabled by default")
    owner_uid = os.environ["SINKA_OWNER_UID"]
    # Refuse to start with live API headers until a separate approval stage.
    if os.getenv("SINKA_OKX_DEMO") != "true":
        raise RuntimeError("Only OKX demo mode supported by this worker")
    ledger = OKXSyncLedger(firestore.Client(), owner_uid)
    lease = OKXSyncLease(ledger)
    journal = OKXOrderJournal(ledger)
    reader = OKXOrderReader(demo=True)
    asyncio.run(run(reader=reader, ledger=ledger, lease=lease,
                    journal=journal))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
