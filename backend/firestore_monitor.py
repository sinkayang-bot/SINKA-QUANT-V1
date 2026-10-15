"""Restartable, paper-only stop monitor for the Firestore API.

Use as a single Cloud Run background task with always-allocated CPU.
Duplicate instances are safe for ledger closure (transactional expected position ID),
but can still duplicate quote requests. No uptime or stop-fill guarantee.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from firestore_app import quote
from firestore_ledger import PaperLedger

logger = logging.getLogger("sinka.monitor")
INTERVAL = max(2.0, float(os.getenv("SINKA_MONITOR_SECONDS", "5")))
OWNER = os.getenv("SINKA_OWNER_UID", "").strip()


async def check_once(ledger):
    snapshot = await asyncio.to_thread(ledger.snapshot)
    position = (snapshot or {}).get("position")
    if not position:
        return "idle"
    px = await quote(position["symbol"])
    triggered = (position["side"] == 1 and px <= position["stop"]) or (
        position["side"] == -1 and px >= position["stop"])
    if not triggered:
        return "watching"
    fill = await asyncio.to_thread(ledger.close, px, "stop (background)", position["id"])
    return "closed" if fill else "already_closed"


def write_heartbeat(ledger, status):
    """Persist worker liveness outside the container; failures must be observable."""
    ledger.account.collection("runtime").document("monitor").set({
        "last_check_at": datetime.now(timezone.utc),
        "status": status,
    }, merge=True)


async def run_monitor():
    if not OWNER:
        raise RuntimeError("SINKA_OWNER_UID must be set before starting monitor")
    ledger = PaperLedger(account_id=OWNER)
    delay = INTERVAL
    while True:
        try:
            result = await check_once(ledger)
            await asyncio.to_thread(write_heartbeat, ledger, result)
            logger.info("paper monitor status=%s", result)
            delay = INTERVAL
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("paper monitor failed; retrying")
            try:
                await asyncio.to_thread(write_heartbeat, ledger, "error")
            except Exception:
                logger.exception("failed to persist monitor error heartbeat")
            delay = min(max(delay * 2, INTERVAL), 60.0)
        await asyncio.sleep(delay)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_monitor())
