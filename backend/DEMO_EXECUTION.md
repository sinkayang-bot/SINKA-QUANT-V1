# OKX demo execution development — offline only

The demo intent, protection, strategy bridge, executor, entry authority, snapshot reader and transport remain
isolated from every deployed API, container, script, paper worker and read-only
adapter. The new HTTP transport is default-off and has no credential discovery.
No real credential or exchange connection was used in development. The executor
still accepts only an explicitly marked rehearsal broker: a transport backed by
`httpx.MockTransport` can be used in tests, while its network-backed form remains
rejected. Test opt-ins are not trading authorization, account verification or
proof of a distributed lease. No journal unlock is supplied.

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
# From repository root, after installing backend requirements:
PYTHONPATH=backend python -m unittest discover -s backend/tests -p 'test_okx_strategy_bridge.py' -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -p 'test_okx_demo_*.py' -v
```


## Executor rehearsal integration

`okx_demo_executor.py` adds a disabled-by-default asynchronous coordinator with
three injected fake-exchange ports: place entry, lookup entry, cancel entry.
It accepts only a broker explicitly marked `environment=demo` and
`rehearsal_only=True`. No deployed service imports it. The separate default-off HTTP transport below
can join it through MockTransport for intercepted tests only. Do not label this
as OKX demo-account acceptance or bypass the rehearsal guard by changing attributes.

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

Remaining before a genuine demo-account trial: trusted runtime wiring of the fixed demo-only authenticated
transport, a complete/fresh account reader, verified audit inputs and deployment
of the local authority/daily-risk integration, fill-price risk checks, bounded protection repair and
emergency-exit state machines, exit-fill audit/journal release, runtime packaging,
and explicit authorization to place demo orders. The original no-exchange-trades
instruction remains effective. Cloud resources and existing services are unchanged.


## Default-off demo HTTP transport — intercepted acceptance only

`okx_demo_transport.OKXDemoTransport` now implements the place/lookup/cancel ports
with a fixed `https://www.okx.com` origin and unconditional simulated-trading
header. There is no live flag, custom host or public generic request API.
Only allowlisted isolated net-mode USDT-swap market entry with exactly one
matching native market TP/SL bracket is supported. Unknown entry fields, mixed
identities, header overrides, wrong modes, and invalid amounts reject before HTTP.
Cancel is restricted to an allowlisted symbol and strategy client ID; order
lookup encodes those validated query parameters into the signature path.

Default construction permits no requests. Enabling additionally requires an
explicit `DemoCredentials` object; the module never reads environment variables,
Secret Manager, files or a browser. Credential representation is redacted. Tests
use only clearly synthetic strings. Do not copy real credentials into source,
CLI arguments, chat, fixtures or logs. Deployment secret references are unchanged.

Signatures cover the exact UTC millisecond timestamp, uppercase method, encoded
path/query and serialized JSON bytes. Request entry expiry is at most 1.5 seconds
or the caller's earlier deadline. Expired/invalid deadlines reject before HTTP;
this is not a substitute for signal/account/admission revalidation. Server-time
synchronization and regional endpoint compatibility still require runtime validation.

The client disables redirects and environment proxy configuration, sets transport
retries to zero, bounds the entire operation to five seconds as well as HTTPX
phase timeouts, and limits trade/individual-order responses to 64 KiB and one result row.
The later GET inventory extension uses separate bounded read schemas (see below). Errors
use a fixed message with suppressed request/response exception context. Returned
fields are allowlisted; upstream messages and arbitrary response data are dropped.
Unknown transport outcome must be reconciled rather than retried.

The network-backed transport advertises `rehearsal_only=False`; the existing
executor refuses it, even if its test enable flag is true. Only an intercepted
MockTransport-backed instance advertises rehearsal mode for the integration
suite. This remains a code/test milestone, not an account-level connection test
or actual demo trade. No new cloud resource, IAM permission or secret was used.

Acceptance: 15 new transport tests passed. Both exact signed POST/GET bytes and
the executor -> transport -> intercepted entry/lookup/cancel sequence were tested,
plus disabled/default behavior, expiry, wrong parameters, timeout/no retry,
redirect/no forwarding, response validation/bounds, exception sanitization and
rejection of a network-backed transport by the rehearsal executor. Across the
selected transport/demo/bridge/read-only/risk/order/instrument regression suite,
**111 tests plus 121 subtests passed locally**. Backend CI collects the new tests.
Prior release b4866f72's previously pending image check also completed successfully.


## Persistent local account authority — synthetic integration

`okx_demo_authority.DemoEntryAuthority` adds a separate execution-authority ledger
on the exact SQLite connection used by `DemoExecutionJournal`. The existing
Firestore **read-only** sync lease remains unchanged and cannot grant submission
rights. No real runtime or private-account reader is wired to this authority.

The lease has an account scope, unique process session, owner, monotonically
increasing fencing epoch, 30-second expiry and clock-rollback rejection. Workers
sharing the same file contend transactionally; same worker names with different
sessions cannot share a current lease. New ownership/expiry reacquisition forces
the kill switch on and clears in-memory snapshot/candidate readiness. A new
session must accept a snapshot received after acquisition; persisted success
cannot be reused. Renewing an unexpired lease retains its epoch. Session IDs must
be new per process; the optional explicit session value exists for test fixtures.
This is a **local shared-file lease**, not distributed coordination across VM
copies/disks. It cannot revoke an exchange request already in flight.

Snapshot input is an attestation from a future trusted reader. It must bind the
account scope, symbol, verified demo/net mode, isolated leverage, USDT equity and
available funds, complete positions/normal-order/algo inventories, monitor health
and fill-audit readiness, received within five seconds. The gate validates these
fields, rejects exposure/pending orders before entry, recomputes the candidate
against the snapshot's contract metadata, loss/margin/available funds and exchange
size caps, and invalidates cached readiness after a bad snapshot. It does not
prove API authentication, pagination, actual leverage, exchange time or fill
completeness by itself. The later two-pass snapshot reader below implements
account/inventory acquisition; history audit is still outstanding.

Daily state is persisted by Asia/Taipei calendar day: baseline equity, at most six
**reserved entry attempts** (a conservative bound including unknown/blocked-after-
reservation attempts), three consecutive audited net losses, sticky 2% equity-
drawdown halt, and 15-minute post-close cooldown. Restart preserves the original
day baseline and counters. A rebound/kill toggle cannot clear a daily halt; the
next calendar day resets day counters/baseline while carrying remaining cooldown
and close chronology. Net-close events require explicit audit attestation and
stable IDs, reject changed replays, and are idempotent; the event producer/fill
math is not implemented here. The authority does not release old order intents.

Entry dispatch reservation, authority/fence/freshness validation, daily-attempt
increment and execution record commit are one `BEGIN IMMEDIATE` transaction.
Failure rolls them all back. If the ledger contains authority state, reopening it
without a bound authority cannot bypass checks. The sixth reservation remains
valid for its second pre-send check but a seventh candidate is denied. Post-
reservation expiry, disabled/kill state or lost authority still prevents the fake
exchange call; its consumed attempt is retained conservatively. Recovery checks
require current owner and a healthy fresh verified snapshot, but ignore entry
kill/daily-loss limits so those entry limits do not themselves block reducing
existing exposure. Real cancel/repair/exit integration remains default-off.

Added local acceptance: 20 authority tests passed, including same-ledger contention,
expired-owner takeover, missing/new-session snapshots, stale and malformed data,
changed balances/specifications, persistent counters/baseline/halts, idempotent
close audit, midnight cooldown, transaction rollback and lease-loss race. An
end-to-end test now runs strategy sizing -> real authority gate -> atomic reserve
-> signed HTTP transport -> MockTransport acknowledgement with exactly one risk
reservation. No account or exchange was contacted. The selected existing plus
new regression suite passed **131 tests and 135 subtests** locally.

Next required work is a complete trusted demo-account snapshot reader and audit
integration, then bounded protective-order repair/exit handling and packaged
runtime acceptance. The network-backed transport is still refused by the
rehearsal executor; no actual demo trading or new cloud deployment is authorized
or performed by this code milestone.


## Complete bounded account snapshot reads — HTTP intercepted acceptance

`okx_demo_reads.py` defines eight fixed GET operations: account config, USDT
balance, isolated leverage, one SWAP instrument, all positions, all pending normal
orders, pending algos, and recent SWAP fills. `OKXDemoTransport.account_read`
accepts operation names and bounded typed arguments rather than a URL/path or
arbitrary filters. These reads retain fixed demo signing, redirects/proxy refusal,
zero retries and timeout controls. Response fields and nested currency/parameter
fields are allowlisted; read responses are at most 1 MiB, 500 positions, 100 rows
per paginated call, one account/instrument/balance row or two leverage rows.
Write/individual-order response bounds remain unchanged at 64 KiB / one row.
No existing owner-facing read-only service or deployment image was modified.

`DemoSnapshotReader` first verifies a configured expected account UID, net mode,
account level 2/3 and supported API permissions before any balance/inventory read.
UIDs remain internal to configuration validation and are absent from the result.
A read-only key can generate diagnostic snapshots but sets
`order_permission_verified=False`; a read+trade key can set that flag true.
The entry authority now requires this flag explicitly in addition to its earlier
gates. No real UID, credential or private-account fixture is stored in the repo.

Then two complete passes compare normalized currency equity/available balance,
isolated net leverage, instrument parameters, positions, normal orders, five
supported algo types (`conditional`, `oco`, `trigger`, `move_order_stop`, `chase`)
and recent SWAP fills. USDT amounts come only from the currency detail row:
account-level USD equity is never treated as USDT. Available funds conservatively
use the lesser of currency `availEq` and `availBal`. Ambiguous/missing currency,
leverage or instrument rows and pending instrument parameter changes reject.

All positions and pending normal/algo inventories are account-wide: a selected
symbol or SWAP-only filter is NOT used to hide other pending exposure. Pagination
requires a terminal short page, valid strictly advancing older cursors, no repeated
identities and no more than 500 records per stream / across algo types. Six page
requests allow exactly 500 rows plus a terminal empty page. Scan overflow,
malformed response, duplicate/cross-type algo IDs, unsupported type or read error
rejects the entire result. Parallel requests use TaskGroup cancellation and the
whole scan has a five-second bound. Results use the OLDEST local receipt/start
time, not a new timestamp assigned after slow scanning. Any inter-pass change,
clock rollback or expiry invalidates cached success; snapshots are still not an
atomic exchange transaction and must be revalidated before future execution.

Recent fills retain contract quantity, price, signed fee/currency, optional
reported P&L and identities; trade IDs are scoped by instrument. Repeated fills,
nonfinite quantities/fees, unsupported fee currency and future fill times reject.
A short API page proves only exhaustion of this recent window, **not complete
history, net strategy P&L, funding audit or absence of unresolved local intents**.
Returned `fill_history_complete` and `fill_audit_ready` therefore remain false.
Even a stable fresh read+trade snapshot cannot unlock the authority by itself.
No code silently adopts unknown orders/positions or upgrades this diagnostic
window to an audited ledger. A durable scoped baseline/owned-fill audit is next.

Acceptance: 18 new snapshot cases passed with all HTTP traffic intercepted. Tests
cover initial UID refusal before private amounts, two-pass changes in each group,
all algo types, cross-page/cross-instrument exposure, full terminal pages, looped
cursors/overflow, response bounds/sanitization, permission gates and recent-fill
incompleteness. Selected new and existing regression checks passed **149 tests
and 164 subtests** locally. All calls were GET through MockTransport; no real
account reads, key retrieval, order, cancel, secret/IAM change or cloud deployment
was performed. Runtime packaging and actual account acceptance remain pending.

## Durable owned-entry fill audit (offline component)

`okx_demo_fill_audit.py` persists an explicit **flat baseline** and scoped entry
fill ledger in the same SQLite database as the execution journal and lease.
Bootstrap requires `baseline_authorized=True`, no existing audit or intent,
zero account exposure and no pending normal/algo orders. Existing state cannot
be reset or re-baselined by this component. Baseline recent history is retained
as history only; it does not become strategy P&L. This flag is a local caller
assertion, not a user authorization UI or permission to trade.

Subsequent audits require a fresh unaudited reader snapshot, a current owner,
a continuity gap of at most 60 seconds and retention of the previous fill anchor
in the bounded recent window. Every dispatched owned entry must have a fresh
individual order lookup at or after the inventory scan started. Ambiguous
submission outcomes and incomplete pending-order inventories block certification.
New fills must match owned entry order IDs, instrument, direction and baseline
time, and report zero closing P&L. Exact decimal quantities must sum to each
order's cumulative filled size and to the account's signed isolated net positions.
Fee and price data are retained immutably, but fee totals are not yet reconciled
against exchange order summaries or account bills.

A failed attempt invalidates the previous receipt and local cached snapshot.
Fill inserts, anchor and revision changes commit atomically; failure preserves
the prior ledger. A successful audit commits a receipt bound to the snapshot
hash, account scope, lease epoch and session. Once the audit receipt table exists,
entry authority checks this proof on snapshot acceptance and immediately before
entry reservation; merely changing `fill_audit_ready` cannot unlock entry.
Takeover requires a fresh audit and keeps the kill switch enabled. An old owner
cannot invalidate the current owner's receipt. This remains a shared-file local
lease, not distributed cloud coordination.

The certified scope is `owned_entries_since_flat_baseline`;
`fill_history_complete` remains false. Unknown/manual fills, native protective
exit-child fills, missed anchors and gaps over 60 seconds halt this component.
It currently provides no archive catch-up/reset path, native-exit adoption,
closed-trade net P&L, funding reconciliation or audited daily-close producer.
Those remain necessary before a complete automatic demo trading lifecycle.
The audit is not wired into a deployed worker or a network-enabled executor.

Acceptance: 15 new synthetic tests, including intercepted GET reader → audit →
authority integration, partial and terminal fills, duplicate prevention, immutable
fees, rollback, missing/excess fills, historical baseline separation, receipt
binding and restart with retained fills. Selected regression passed **164 tests
and 185 subtests** locally. No actual OKX account read or order/cancel request,
credential retrieval, cloud deployment or billing change was performed.

## Native OCO closed-trade audit (offline, before funding)

`okx_demo_close_audit.py` extends the scoped fill auditor with an immutable
closed-trade record. Construct `DemoClosedTradeAuditor` BEFORE flat-baseline
bootstrap: its baseline hook stores original validated contract metadata in the
same transaction. An existing baseline without that context is not retroactively
populated from current metadata and cannot certify a close through this component.

The supported subset is one fully filled entry, one effective fixed-size
reduce-only OCO and one fully filled market child. Caller-supplied trusted demo
algo and child detail groups must be fresh, complete, account-bound and received
at or after the account scan started. Both directions of lineage are checked:
the planned attached client ID identifies the algo, its `ordIdList` must contain
exactly the child ID, and that child must identify the same algo and client ID.
Stop/target, trigger basis, direction, isolated/net mode and quantities must
match. Split child orders, spawned algos, partial exits, canceled partial entries,
unknown fills and ambiguous evidence reject. Empty child `clOrdId` is supported;
it is never invented as a correlation identity.

The account must be flat with neither normal nor protective orders pending.
Original instrument parameters must still match. The existing entry journal,
bounded fill-window anchor and 60-second continuity checks remain mandatory.
Each side's fills must total the known entry size exactly. Close fills cannot
precede completion of entry. Weighted contract-price totals and original
contract value determine signed gross P&L. Reported close-fill P&L must agree
within 0.00000001 USDT; larger discrepancies fail closed. Every signed USDT fill
fee is included once, including positive rebates. `trading_net_usdt` is gross
plus signed entry/exit fees, **before funding and other account-bill adjustments**.
It is not the account's complete net result.

Fill inserts, trade record and continuity checkpoint commit atomically.
Repetition is idempotent; previously stored fills and closed-trade totals cannot
be rewritten. A reopened ledger retains the close and requires fresh current
owner/order/detail evidence. Every close attempt invalidates entry readiness;
success deliberately does not issue a readiness receipt, delete the intent or
call `record_verified_close`. `new_entry_allowed`, `submission_enabled`,
`funding_audited` and `account_bills_audited` remain false. Bill/funding audit,
daily-risk close publication, authorized intent lifecycle release, production
read adapters and deployed runtime integration remain outstanding.

Official protocol reference: [OKX API guide](https://www.okx.com/docs-v5/en/),
GET Order details, GET Algo order details (`ordIdList`, `algoId`, attached client
ID linkage) and transaction details. This component consumes offline evidence;
there is no new HTTP method, endpoint, credential access or deployment wiring.
Actual demo-account response compatibility has not been accepted yet.

Acceptance: 16 new synthetic close-audit tests cover long/short direction,
losses, weighted fills, fees/rebates, wrong lineage/trigger parameters, partial
and non-reducing children, cleanup, rollback, restart and immutable records.
Selected regression passed **180 tests and 223 subtests** locally. No real
account access or exchange order/cancel request was made.

## USDT bill/funding settlement and atomic risk publication (offline)

`okx_demo_settlement.py` extends the single-trade close auditor. Construct this
class before the explicit flat-baseline bootstrap so the original USDT currency
`cashBal` is persisted atomically with instrument and fill baseline metadata.
`DemoSnapshotReader` now retains this currency value as `cash_balance_usdt`,
separately from equity and available amounts, and includes it in double-pass
comparison. Missing cash remains diagnostic `None`; settlement baseline creation
rejects it rather than substituting equity. Earlier baselines cannot be upgraded
by inventing an original cash amount.

A trusted bill-reader contract supplies two independent complete paginated
responses for the entire account's USDT interval, with exactly currency, begin,
end and limit query parameters. Instrument/type filtering is not allowed.
The interval starts at the stored baseline and ends at the current account scan
start, at least five seconds after the audited close. Both response passes must
agree. Each pass requires strictly descending unique bill IDs, full intermediate
100-row pages, a short terminal page and at most 500 rows / six requests.
This is validation of supplied offline evidence, **not an implemented production
bill transport or an authentication boundary**. The delay is conservative
settling time, not a guarantee that OKX cannot publish another bill later.

Supported trade bills (type 2, subtypes 1–6) must match each known fill's instrument,
order/trade identity, side, contract quantity, price, fee and reported P&L exactly.
Each fill needs exactly one bill. Supported funding bills (type 8, expense 173 or
income 174) must have the correct sign, no order/trade identity, zero trading fee,
matching full held contract size and a timestamp within the completed-entry to
close interval. Partial-position funding and late posting outside that interval
are currently unsupported. Unknown transfers, liquidations, adjustments, other
instruments/currencies, changed historical bills or unbalanced rows reject.
The supported subset requires `balChg == pnl + fee`; other isolated/account
movement representations are rejected pending actual response acceptance.

Sum of bill balance changes must equal the closed trade's signed gross P&L plus
both-side fill fees plus signed funding, and must equal final currency cash minus
original flat-baseline cash. Cash equality is exact; no residual is hidden in a
rounding bucket. Close fill anchor/continuity, immutable fill records, fresh owned
order observation and flat complete inventory checks remain mandatory. An
unknown post-close fill cannot be silently ignored even if cash still balances.

On success, immutable bills, settlement and `demo_risk_close` publication commit
in ONE SQLite transaction. Failure in daily-risk publication rolls them all back.
The authority's internal transactional close primitive is shared with the existing
public wrapper. Settlement updates loss streak, sticky halt on the third loss,
and 15-minute cooldown once, using net P&L including funding. Retry/restart cannot
double-count. Publication currently requires the close to be on the current
Taipei day with its existing daily baseline; delayed cross-day publication fails
closed rather than modifying a historical day's risk state.

`funding_audited`/`account_bills_audited` describe the supplied interval **as of
`reconciled_through_ms`**, not eternal account-history completeness. Late changes
invalidate a later attempt and are not silently rewritten. Entry readiness stays
invalid, the old intent remains, and `new_entry_allowed`/`submission_enabled`
remain false. Next work is a real GET-only bill adapter, multi-intent archival
and release with current settlement proof, cross-day recovery and deployed
integration. No actual exchange trading was enabled by these offline checks.

Protocol reference: [OKX Trading Account REST API](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-7-days),
USDT bill `pnl`, signed `fee`, `balChg`, identifiers and funding subtypes.
Actual demo account bill/isolated balance representation is not accepted yet.

Acceptance: 17 new synthetic tests cover funding expense/income/zero, funding
turning a profit into a loss, third-loss halt, exactly-once cooldown/publication,
matching all fill bills, unknown adjustments, complete pagination, cash mismatch,
post-close fills, double-pass changes, rollback and restart. Intercepted GET
snapshot checks verify currency cash is retained separately and changes reject.
Selected regression passed **197 tests and 244 subtests** locally. No account
credentials, actual account reads, order/cancel requests, GCP deployment or
billing change were involved.

## Fixed GET bill adapter and snapshot/settlement integration

`okx_demo_bill_reader.py` implements the production-shaped read adapter for the
previously offline bill contract. It accepts only `DemoSnapshotReader` using the
existing default-off fixed-host demo transport. `read_bills` signs a fixed
`GET /api/v5/account/bills` request with `ccy=USDT`, explicit `begin`/`end`,
`limit=100` and optional strict numeric `after`. No instrument/type filter,
arbitrary endpoint, live mode, new POST operation, credential discovery or
runtime deployment is added. Each response uses the existing bounded/sanitized
GET schema; unneeded upstream fields and messages are discarded.

Collecting a bundle first verifies the configured UID and net/account mode via
an entire two-pass account snapshot. It then scans the fixed account-wide bill
interval twice and collects another two-pass account snapshot. Both complete
account results (including cash, equity, inventory, permissions, instrument and
recent fills) must match after removing local read timestamps. UID/config is
rechecked by the second snapshot reader; a wrong initial UID prevents both bill
and amount reads. Currency cash must be available. A read-only key can produce
diagnostics, but its snapshot retains `order_permission_verified=False`.

The cutoff is the FIRST account snapshot's start, not a fresh timestamp assigned
after a slow scan. Both bill passes cover the stored begin through that same
cutoff. Pagination enforces strictly decreasing unique IDs within/across pages,
row timestamps inside the requested interval, a short terminal page, a maximum
of 500 rows and six requests per pass. Exactly 100 or 500 rows therefore require
an additional terminal empty page. Baselines older than the recent seven-day
endpoint retention are rejected before requests. The whole bundle has a
five-second timeout and clock-age bound. Failure/cancellation clears cached
bundle and reader success; cancellation propagates normally. Returned values
and cached success are separately deep copied.

The result contains `snapshot` and `bills`, directly consumable by
`DemoSettlementAuditor.settle`. A fresh individual owned-entry observation is
still needed independently; bill reads do not authorize an exchange request.
Snapshot `fill_audit_ready` and `fill_history_complete` remain false, and
settlement does not release an intent. This adapter currently reads bills only;
native algo/child detail read adapters, archival/multi-intent release, cross-day
settlement and deployed runtime wiring remain outstanding. Stable snapshots
and bill passes are bounded observations, not an atomic exchange transaction
or a guarantee against future late postings.

Acceptance: 17 new tests intercept every HTTP request. They cover UID checks
before amounts, permission preservation, account changes, complete 100/500-row
pagination, overflow/duplicates/looped cursors, interval/schema bounds, double-pass
changes, default-off behavior, HTTP errors/redirects, privacy, timeout and external
cancellation. An intercepted HTTP bundle also feeds the persisted close and
settlement ledger and publishes funding-inclusive loss risk exactly once.
Selected regression passed **214 tests and 265 subtests** locally. No real OKX
account reads, keys, orders/cancels, GCP resources or billing were accessed or
changed during this implementation. Actual account acceptance remains pending.

## Fixed GET protective-close details and atomic journal ingestion

`okx_demo_close_reader.py` queries the tracked entry by strategy client ID, the
attached native algo by its planned attached client ID and its sole child by
exchange order ID. These are fixed GET endpoints using the existing default-off
demo transport. Caller-supplied URLs, write operations, credential discovery and
new runtime deployment are not added. Algo responses retain a bounded list of
strict numeric child IDs. Child order responses retain `algoId`, `algoClOrdId`
and reduce-only status; an empty child `clOrdId` is kept unchanged.

The reader validates the configured account before any detail/amount read,
collects two stable entry/algo/child passes, then repeats the entire account
snapshot. Account data and detail rows must remain equal. The entry must be
fully filled and agree with its planned client identity, direction, mode and
size, plus a known acknowledgement order ID if supplied. A single child must
match the algo in both directions. The same `validate_lineage` implementation
is now shared with the durable close auditor, so trigger prices, reduce-only
market exits and child identity checks cannot drift between components.
Split children, spawned algos, active/partial/failed orders, foreign lineage and
ambiguous detail results fail closed. Both detail passes are bounded by the
whole five-second scan. Evidence retains the first pass's request-start times;
no timestamp is refreshed just because scanning completed. Failure or external
cancellation clears cached successes. Account snapshots continue to report
`fill_audit_ready=False`; a read-only key is never upgraded to trade permission.

`DemoClosedTradeAuditor.audit_closed` can consume this bundle directly. Its
optional `entry` evidence is validated and persisted INSIDE the same current-owner
SQLite transaction as fill inserts and the immutable close record. The shared
journal observation primitive retains all previous identity/quantity/monotonic
state checks. The public observation wrapper retains its existing transaction
behavior. A failed close rolls back the new entry observation and new fills;
a stale lease owner cannot ingest either. A previously uncertain receipt can
be resolved through a matching fresh lookup without resubmission, while a known
receipt order ID cannot be replaced. No readiness receipt or intent release is
issued by successful close ingestion.

Acceptance: 15 new intercepted HTTP tests cover fixed request correlation,
UID/default-off gates, empty child client IDs, split/foreign/partial children,
changed protection parameters, two-pass/account changes, response failures,
invalid queries, permission preservation, expiry/timeout/cancellation, atomic
rollback and stale-owner fencing. The GET reader bundle feeds the durable close
ledger without needing the rehearsal-only executor to perform a real read.
Selected regression passed **229 tests and 296 subtests** locally. No real
account reads, credential access, order/cancel request, GCP resource or billing
change was performed. Actual account compatibility, multi-intent archival and
release, cross-day settlement and deployed runtime integration remain pending.

## Explicit cycle archival, idle bill proof and next-signal eligibility

`okx_demo_cycles.py` adds `DemoCycleLifecycle`, with cycle transitions disabled
unless the caller explicitly sets `enable_cycle_transition=True`. It sends no
exchange request and is not wired to a deployed worker or network executor.
`advance_cycle(snapshot, bills)` revalidates the current full bill/cash settlement
INSIDE the same SQLite transaction used for archival. Daily-risk publication,
complete archival of the current active ledgers and creation of the next flat
baseline therefore commit together. An injected failure after the archive insert
and active-ledger clearing rolls the whole transaction back, including a new risk
publication. The original active intent/fills remain available for recovery.

Archives retain the intent/plan and observation, execution record, fill metadata,
all fills and fees, original contract/cash context, account bills, close,
settlement and readiness receipt, with the matching persistent risk-close record.
Verification metadata binds the current account snapshot and bill evidence by
hash. Archives form an ordered SHA-256 chain and retain every used client ID.
This detects accidental corruption; it does not protect against an administrator
who can deliberately rewrite the entire database and chain. Current sequence,
latest hash and cycle boundary must match before another transition/idle audit.
An archived strategy client ID cannot be registered or dispatched again.

Only after its archive has been inserted in the transaction are the old active
cycle tables cleared. Lease/session, all archives, daily baselines, entry counts,
loss streak, halt, cooldown and risk-close history are NEVER cleared. Current
recent fills become historical baseline rows for the new cycle, using the
verified current flat currency cash and instrument metadata. This is a named,
validated cycle transition, not a fallback reset after a failed reconciliation.
Generic bootstrap still refuses to reset an existing audit.

Transition returns a fresh snapshot proof, but `new_entry_allowed` and
`submission_enabled` remain false. The caller must accept that proof and apply
the existing authority checks. A subsequent signal must be newer than the cycle
boundary. The 15-minute cooldown, third-loss halt, six-entry daily cap, kill
switch, fresh balance/instrument checks and atomic dispatch reservation still
apply. Takeover restores the kill switch and requires current-session proof;
archival cannot revive an old worker or grant a duplicate dispatch.

While waiting for a new signal, `audit_idle(snapshot, bills)` renews entry
readiness only with an account-wide two-pass EMPTY bill interval from the current
cycle boundary, unchanged original cash, no unknown/changed fills, a flat account
and no normal/protective orders. It retains the 60-second audit continuity bound
and seven-day recent-bill endpoint bound. Funding/transfer/adjustment activity
while idle fails closed and requires separate reconciliation. Each accepted
entry proof is bound to cycle sequence, lease epoch, session and exact snapshot
hash in addition to the existing fill-audit receipt. A generic fill audit alone
cannot refresh entry eligibility after a cycle transition. These are observations
as of their cutoffs, not a guarantee against later exchange postings.

The settlement implementation now exposes an internal transaction-sharing
primitive; its existing public wrapper preserves the earlier behavior. No
exchange POST operation, API key read, cloud change or billing change is added.
Actual demo-account response acceptance, runtime orchestration, distributed
coordination, cross-day delayed settlement and longer offline catch-up remain
outstanding. Unsupported cases preserve state and block entry.

Acceptance: 18 new synthetic tests cover rollback, archival integrity, replay
prevention, idle cash/bill/fill changes, cooldown, daily cap, kill switch, third
loss, current-session receipt requirements and failed generic-audit bypass.
An intercepted bill reader feeds idle proof renewal. A complete two-cycle
rehearsal includes first close/settlement/archive, process/lease restart,
continuous idle monitoring through cooldown, a newer second signal, its full
entry/close/settlement and a second archive. Both histories and two daily risk
entries/closes remain intact. Selected regression passed **247 tests and 301
subtests** locally. Every order in that rehearsal is an in-memory FakeExchange
operation; no actual OKX demo or live order/cancel/account request was made.

## One-shot read reconciliation runner and isolated runtime package

`okx_demo_reconciliation.py` integrates verified reads, entry observation,
closed-trade audit, bill/funding settlement and cycle archival. It is disabled
by default. Enabled runs acquire/renew the shared-file lease and invalidate old
entry readiness BEFORE network reads. Runners sharing one authority also share
an async lock. An occupied lease or unsubmitted intent causes no private read
and no implicit dispatch. A missing baseline returns `baseline_required`;
`initialize_baseline=True` is an explicit first-bootstrap assertion, never a
reset of existing intents or history. Initialization preserves the kill switch.

For a dispatched entry, a composite five-second read brackets two detail passes
and two complete bill passes with two complete verified account snapshots.
Every group uses the SAME initial account cutoff, avoiding the previous need
to manually refresh an entry between independent close and bill bundles. The
account, detail rows and normalized bill sets must remain stable. Active,
ambiguous, unprotected or unsupported exits block and retain the original intent.
The runner currently reconciles supported CLOSED cycles and clean idle accounts;
it does not monitor/repair open protection or execute a strategy entry.

The runner starts ONE current-owner SQLite transaction after the reads. Entry
observation and fill ingestion, immutable close, bill settlement, daily risk and
archive/baseline rotation are committed together using shared internal audit
primitives. A late failure rolls all of them back. Restart re-queries the same
client ID; there is no submission/resubmission path. A crash after commit but
before snapshot acceptance recovers into the new idle cycle without another
archive or risk-close publication. Idle cycles, including an initialized first
cycle, use full empty bill/cash validation. No runner status grants entry:
`new_entry_allowed` and `exchange_submission_enabled` remain false, with
`orders_sent=0`. Errors are fixed sanitized statuses and cancellation propagates
while invalidating old readiness. A stale owner cannot invalidate a new owner's
proof or commit scan results after takeover.

`okx_demo_reconciliation_worker.py` is the bounded one-shot server entrypoint.
`RUN_DEMO_RECONCILIATION=1` is required before even resolving server credentials.
It uses the existing server environment names `OKX_API_KEY`, `OKX_API_SECRET`,
`OKX_API_PASSPHRASE`; values must remain server-side and are never printed. It
also requires configured `OKX_EXPECTED_UID`, `DEMO_ACCOUNT_SCOPE` and an absolute
`DEMO_LEDGER_PATH` whose parent exists on a persistent mounted local filesystem.
Optional `DEMO_SYMBOL` defaults to BTC-USDT-SWAP; `DEMO_WORKER_ID` defaults to
read-reconciliation. `DEMO_CYCLE_TRANSITIONS=1` enables LOCAL archival only;
`DEMO_INITIALIZE_BASELINE=1` asserts first bootstrap only. There is no flag to
clear the kill switch, place/cancel an order or schedule another execution.
Each process uses a new lease session; later one-shot runs must respect its
30-second lease and 60-second continuity bound. No scheduler is installed.

The entrypoint uses `OKXDemoReadTransport`, which structurally rejects entry,
cancel and every non-GET/internal request with a body. It cannot submit even
when the configured key has trade permission. It makes no credential discovery
call. Status output contains no key, UID, raw provider error or private amounts.
Disabled exits with code 0; missing/inconsistent configuration or blocked reads
exit 2; occupied lease/busy exits 3. Supported initialization/idle/archive exits 0.

`Dockerfile.okx-reconciliation` packages only the Python read/audit dependency
set, runs as UID 10001 and provides `/data` for a separately mounted retained
ledger. It is not deployed by this change. The added GitHub image workflow builds
it and verifies disabled and missing-configuration startup with network disabled
and a read-only root. No Artifact Registry push, GCP job/service mutation,
secret binding or billing change is included. Actual account response/storage
acceptance, strategy/open-position runtime integration, cross-day recovery and
longer offline catch-up remain outstanding. SQLite on an ephemeral task disk is
not an acceptable substitute for persistent storage.

Acceptance: 17 new runner tests plus 6 read-transport/runtime tests cover the
full intercepted GET-to-archive transaction, initial setup, idle continuation,
missing bills, failed archive rollback, crash after commit, restart, competing
workers, cancellation, stale-owner fencing and structural write refusal.
Selected regression passed **270 tests and 301 subtests** locally. Local disabled
entrypoint returned `phase=disabled`, zero orders and both entry/submission flags
false. Image validation is performed by CI because local Docker is unavailable.
All account requests in tests are intercepted; no actual OKX read, order/cancel,
credential retrieval or cloud deployment was performed.

### Archived idle restart window

An explicitly requested `--resume-idle` can verify an archived flat cycle after
an interruption of up to 48 hours since its last successful audit. Archive age
alone does not restart this interruption clock or invalidate an earlier audit.
The original archive boundary must still fit the bill reader's seven-day
interval; missing recent-fill anchors still block recovery independently.
The never-traded initial cycle
retains its 12-hour limit. This is a bounded GET-only catch-up, not a new baseline:
the archive chain, original fill anchor and immutable known fills, exact cash,
flat inventory, no pending orders/algos, and two empty account-wide bill scans
must all pass. No cash tolerance, ledger reset, or order retry is added. Missing
historical evidence or a baseline outside the bill interval still blocks entry.

Regression coverage includes a real successful audit at archive age 36 hours,
then a second resume at age 51.13 hours. The original baseline, fills, cash,
archive and risk records remain unchanged. Gaps over 48 hours, bill intervals
over seven days, missing anchors, unknown fills, bills and cash changes remain
blocked. These offline tests do not establish cloud acceptance or send orders.

Idle audit failures now expose a fixed `audit_stage` label through the control
runner, including `archived_idle_window`, `idle_baseline`, `idle_bills`, or
`idle_cash`. Raw exceptions, credentials, account identifiers and provider
responses are never included. Source changes require a fresh Cloud Shell runner;
an already-running clone does not update automatically.

### Stop-distance gate diagnostics

Rejected strategy stops retain the existing 0.2% to 3% bounds after tick
rounding and adverse entry slippage. Both live progress and the final retained
`last_entry_block` report include `entry_diagnostics`: fixed `stop_rejection`
labels (`below_minimum`, `above_maximum`, `invalid_rounded_price`),
`stop_distance_pct`, `min_stop_pct`, and `max_stop_pct`. The final block also
retains the selected instrument, strategy and scan tick. No account amounts,
raw exception messages or credentials are added. Rejected candidates create
no intent or order. Earlier runs did not retain these measurements and cannot
be diagnosed retrospectively from the aggregate counter alone.

Entry failure diagnosis: `entry_stage` separates archive verification, bill reads, idle audit, snapshot admission, planning, reservation and final revalidation. `audit_stage` and `read_stage` preserve fixed substage labels. `plan_rejection=source_freshness` identifies the planner freshness check rather than incorrectly implying a cash mismatch. No exception text, credentials or exchange response bodies are reported. These diagnostics do not change admission or dispatch behavior.

Startup preflight repeats a whole GET-only snapshot at most three times for typed transport/timeout failures or double-read consistency rejection. Each attempt retains the original five-second age/deadline limits; partial evidence is discarded. Account identity, permission and inventory schema validation do not retry. Exhaustion reports `read_diagnostics` with fixed snapshot stage, issue (changed/expired), failure kind and attempt count. No ledger, baseline or exchange write is retried or bypassed.

Runner disconnections: Cloud Shell stdout now reports the fixed failing stage (connect/poll/launch_command/read_child_result/publish_result), failure category and an allowlisted SDK exception name. Normal runtime expiry and requested stop are reported separately. No command polling or exchange action is replayed. Abrupt Cloud Shell termination may prevent any final message; the control page therefore shows the last heartbeat in Taiwan time and explicitly marks offline `ready` as historical. These changes diagnose disconnection; they do not provide continuous cloud hosting or automatic reconnection.

Market-candidate continuity: a market signal older than 20 seconds or quote older than 15 seconds, detected in the pure planner before reservation, reports `entry_gate_blocked / signal_expired` and does not activate the account kill switch. Future timestamps, stale account evidence, schema/identity failures and any validation failure after reservation still block and preserve reconciliation evidence. No expiry threshold is extended.

In the 20-market scanner, a candidate rejected for stop distance, minimum funded size or market-source expiry is deferred for that symbol/strategy/candle identity. Remaining fresh candidates can be evaluated on subsequent ticks; the same candidate returns when a new candle identity appears. Deferral is process-local, bounded and pruned against current scanner results. Price-drift rejections are not deferred because an improved quote can change that decision. Ledger deduplication, account ownership, stop limits and sizing remain unchanged. `deferred_candidates` counts skipped current candidates.

Regression evidence: intercepted-transport tests demonstrate expiry without an order or intent, continued idle verification followed by a fresh admitted signal, continued fail-closed behavior for a future timestamp and post-reservation expiry, and a rejected BTC candidate followed by an admitted ETH candidate through the actual supervisor and scanner. These are offline acceptance scenarios, not evidence that the currently disconnected Cloud Shell worker is running this version.
