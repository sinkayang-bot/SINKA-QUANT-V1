# OKX demo execution development — offline only

The demo intent, protection, strategy bridge and executor modules remain isolated
from every HTTP API, container, deployment script, paper worker and read-only
adapter. There is no network transport, credential lookup or journal unlock.
The new executor only coordinates an explicitly marked fake demo broker in a
rehearsal harness. Its opt-in and gate callbacks are test controls, not trading
authorization or proof of authenticated demo account / distributed lease state.

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
  actual cancellation of an unfilled remainder, emergency reduce-only exit, automatic
  journal release, live strategy bridge or continuous demo runner. An offline sizing bridge now
  exists, as described below.
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


## Offline strategy-to-contract bridge

`okx_strategy_bridge.plan_strategy_signal` consumes the existing JavaScript
SNR-5M-1 engine output directly. It does not duplicate the EMA/range/ATR rules or
reuse the research account's base-coin quantity as the number of contracts.

Checks cover the strategy version, matching instrument/quote symbol, 5m candle
identity, signal confirmation within 20 seconds, quote within 15 seconds and
not before confirmation, a genuinely new signal after worker start, metadata
receipt within 60 seconds and balance receipt within five seconds. Caller-supplied
times/data require a future trusted runtime reader; freshness alone cannot prove
correctness or account identity. Entry drift, including adverse tick rounding,
must not exceed half ATR. Rounded stop distance must remain 0.2%–3% of modeled
slipped entry. Zero/nonfinite amounts and invalid contract specifications reject.

The entry anchor rounds adversely to tick size; the structural stop rounds
outward. Contracts floor to lot size after taking the tightest cap of modeled
fee/slippage-inclusive 0.5% equity loss, 10% equity margin at at most 3x leverage,
available balance including entry fee, and required `maxMktSz`/`maxStopSz` metadata.
If this cannot fund `minSz`, no upward rounding or order is allowed. Returned
base-coin quantity is reported separately. No existing paper sizing rule changes.

The initial TP remains exactly 2R from the rounded planning anchor, matching the
existing offline demo planner. Market fill price is unknown; this is **not** proof
of exact 2R from a future actual fill. Runtime fill-price risk/protection checks,
rejection/repair of excessive slippage, daily limits/cooldown, admission/lease,
metadata changes and execution-time revalidation remain necessary. The returned
expiry is a diagnostic bound, not an enforced exchange deadline or permission.

Local acceptance: 11 new bridge cases plus the existing 25 offline demo cases
passed. The integration test launches the actual JS engine with synthetic long
and short candle sequences, feeds both output signals into Python sizing, checks
risk bounds and persists/reopens the resulting order intent to test no-repeat
dispatch. Five symbols use synthetic matching contract metadata, not claims of
current exchange contract values. Nothing was submitted to OKX or deployed to
Cloud Run/the VM. Backend CI pins Node 22 for the cross-language test and now also
runs when the shared strategy engine changes.

```sh
# From repository root, no external Python dependencies required:
PYTHONPATH=backend python -m unittest discover -s backend/tests -p 'test_okx_strategy_bridge.py' -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -p 'test_okx_demo_*.py' -v
```


## Executor rehearsal integration

`okx_demo_executor.py` adds a disabled-by-default asynchronous coordinator with
three injected fake-exchange ports: place entry, lookup entry, cancel entry.
It accepts only a broker explicitly marked `environment=demo` and
`rehearsal_only=True`. No HTTP/SDK broker is supplied and no deployed service
imports it. Do not label this as OKX demo-account acceptance or enable it with a
real broker by merely changing these attributes.

The flow reuses strategy sizing, persistent intent/dispatch fencing, monotonic
order observation and independent protection assessment. A transaction durably
reserves entry before any fake place call. It checks source expiry and the gate
again immediately before calling the port; disabling during the gate blocks it.
Acknowledgements require top-level success, exactly one individual success,
matching client ID and a numeric exchange order ID. They are not fills. Rejected,
malformed, timed-out or ambiguous responses remain unresolved, never auto-retry.
A crash before receipt leaves the reservation intact. Fresh client-ID lookup
must agree with any acknowledged order ID and the journal's intent parameters.
A bad lookup invalidates prior cancellation freshness. Raw exception text and
exchange response messages are not persisted or returned.

Cancellation has its own durable one-shot reservation, requires a matching
observed active order within five seconds, and checks the recovery gate again.
A cancel acknowledgement does not update order state: a later lookup must prove
cancellation or additional/full fills. Timeout or restart never grants another
cancel attempt automatically. Canceled partial fills remain exposure needing
independent native protection and position/fill reconciliation. The rehearsal
does not cancel an algo, repair protection, emergency-close, unlock the journal
or run a continuous strategy loop.

Added acceptance: 21 asynchronous cases exercise complete synthetic flows,
including source -> lot sizing -> intent -> fake receipt -> lookup -> partial
fill -> independent protection assessment, cancel/fill races, crash/reopen,
ambiguous receipt, disk failure, competing cancel reservations, slow/lost gates
and disabled/live-broker rejection. Together with previous modules, 57 unittest
cases passed locally (46 demo cases plus 11 strategy bridge cases). All exchange
ports were fake; no keys, private account requests or orders were used.

```sh
PYTHONPATH=backend python -m unittest discover -s backend/tests -p 'test_okx_demo_*.py' -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -p 'test_okx_strategy_bridge.py' -v
```

Remaining before a genuine demo-account trial: a fixed demo-only authenticated
transport, trusted complete/fresh account reader, actual lease/admission and
daily-risk integration, fill-price risk checks, bounded protection repair and
emergency-exit state machines, exit-fill audit/journal release, runtime packaging,
and explicit authorization to place demo orders. The original no-exchange-trades
instruction remains effective. Cloud resources and existing services are unchanged.
