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
