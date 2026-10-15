import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator is not configured",
)


def test_lease_handoff():
    from google.cloud import firestore
    from firestore_ledger import PaperLedger
    from monitor_lease import MonitorLease

    db = firestore.Client(project="sinka-emulator")
    ledger = PaperLedger(db=db, account_id="test-" + uuid.uuid4().hex)
    first = MonitorLease(ledger, worker_id="worker-a")
    second = MonitorLease(ledger, worker_id="worker-b")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()

def test_paper_close_is_idempotent():
    from google.cloud import firestore
    from firestore_ledger import PaperLedger
    from monitor_lease import MonitorLease

    db = firestore.Client(project="sinka-emulator")
    ledger = PaperLedger(db=db, account_id="test-" + uuid.uuid4().hex)
    ledger.initialize()
    position = ledger.open("BTC-USDT-SWAP", 1, 100000, 99000, "open-request-123")
    first = MonitorLease(ledger, worker_id="worker-a")
    second = MonitorLease(ledger, worker_id="worker-b")
    assert first.acquire()
    assert ledger.close(98900, "paper stop", position["id"], second.worker_id) is None
    assert ledger.snapshot()["position"]["id"] == position["id"]
    assert ledger.close(98900, "paper stop", position["id"], first.worker_id) is not None
    assert ledger.close(98900, "paper stop", position["id"], first.worker_id) is None
    assert ledger.snapshot()["position"] is None
