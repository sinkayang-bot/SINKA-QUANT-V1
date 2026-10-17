# OKX demo execution development — offline only

`okx_demo_intent.py` and `okx_demo_protection.py` are isolated building blocks. It is not wired into
any HTTP API, container, deployment script, paper worker or read-only adapter.
It has no network transport, credentials, environment credential lookup, submit
method or journal unlock. Its outputs cannot authorize exchange execution.

Implemented:

- Explicit `demo=True`; an offline request descriptor with fixed simulated
  trading header, isolated linear-USDT swap, net position mode, allowlisted
  instrument and contract/tick checks. Account net mode and actual leverage
  are requirements to verify later, not settings changed by this module.
- Fixed 2R TP and native attached TP/SL descriptor with last-price triggers and
  market exits. Fee/slippage-inclusive modeled risk capped at 0.5% equity,
  retaining existing 10% margin and 3x leverage limits. Gaps can exceed modeled loss.
- Stable account/signal/instrument-derived client and attached algo IDs. Reusing
  an identity with changed parameters is rejected. Account scope must remain
  stable across process/VM restarts; do not generate it on each boot.
- SQLite WAL/FULL journal. Atomic one-shot dispatch reservation survives reopening;
  a second connection cannot reserve it again. This is exercised with synthetic
  lifecycle events only. One unresolved intent blocks all further entries.
- Observations must match identity, instrument, side, mode, type and quantity;
  fill regression and changes after a terminal state are rejected.
- Timeout/missing receipt requires lookup by client ID and never permits retry.
  A zero-fill cancellation still requires flat-account and algo cleanup proof.
  Partial fills, cancellations after partial fills and full fills all block entries
  pending independent position/protection verification. Attached TP/SL in the
  request or the main order being filled does not prove native protection exists.

Not implemented or enabled:

- No exchange writes, actual demo order, live protective-order collection/repair,
  cancellation of an unfilled remainder, emergency reduce-only exit, automatic
  journal release, strategy-to-contract sizing bridge or continuous demo runner.
- This journal is not a distributed account lock or a substitute for existing
  lease-fenced admission/synchronization checks. Plans are local research data;
  a future executor must independently revalidate the whole plan, fresh signal,
  metadata, balance, account configuration, leverage and all risk/admission gates.
- Existing no-exchange-trades instruction remains in effect. Real trading is
  outside scope. No secrets or private-account fixtures are required for tests.

Offline validation (from `backend/`):

```sh
python -m unittest discover -s tests -p 'test_okx_demo_*.py' -v
python -m pytest -q tests/test_okx_demo_intent.py tests/test_okx_demo_protection.py tests/test_okx_orders.py tests/test_okx_risk.py tests/test_okx_instruments.py
```

Observed locally: 44 tests plus 21 subtests passed. The existing backend CI
collects this unittest file through pytest automatically.

Official API reference consulted 2026-10-17:
https://www.okx.com/docs-v5/en/ (demo header, client IDs, attached TP/SL).
This is development progress, not a claim of profitable or production-ready trading.


## Protection and recovery assessment

The offline assessor compares independent entry, positions, pending normal orders
and pending algo snapshots. The caller must attest to the same verified demo
account/session, complete pagination of account scope (not tracked-ID-only scans),
local receive times no older than five seconds, and at most two seconds between
snapshot receipt times. API order creation/update times are not freshness proof.
These fields are trusted-reader inputs, not cryptographic evidence; no reader
has been wired to this module. Concurrent exchange changes still require repeated
reconciliation, and this assessment never grants entry/submission authority.

Supported protection is deliberately narrow: one live OCO, fixed contract size
exactly equal to the signed net isolated position/entry fills, opposite exit side,
explicit reduce-only, matching planned last-price TP/SL triggers and market exits.
Split targets, one-way conditional, trailing, full-position-fraction and ambiguous
algos fail closed rather than being counted as equivalent. Triggered/non-live
algos require child-order/fill reconciliation before considering another exit;
no duplicate emergency close is generated. An entry's attached bracket fields
alone never satisfy this test.

Partial fills can be marked protected only for the currently observed size;
remaining entry quantity still requires cancellation and another complete read.
Flat exposure does not unlock: exit-fill audit and orphan-algo cleanup remain
required. Extra positions, extra pending orders and foreign/duplicate algos block.
Malformed/stale data yields a sanitized halt/refresh recommendation, not raw
exchange content or an executable request. Fault handling currently produces
recovery recommendations only; actual repair/cancel/emergency-exit execution
is not implemented or enabled.

`DemoIntentStore.protection_report` binds assessment to the persisted plan and
last observed exchange order identity/fill state, survives reopen, and neither
changes the dispatch reservation nor releases the entry block. A caller must
persist a matching current entry observation before assessing fresh snapshots.

Validation: 25 standard-library unittest cases pass, covering both demo modules,
with parameterized subtests for stale/malformed data, incorrect protection,
partial/canceled fills, long/short sides, unexpected exposure, triggered algos,
orphan cleanup, and persistent identity binding. Existing CI also collects these
via pytest. No OKX request was made during these tests.
