# Owner-only demo asset panel

Deployment verified from the owner's Cloud Shell screenshot received on
**2026-10-17 09:22:57, Asia/Taipei**. Source commit
`2c594184b3e8c5ce316a57c3e8181e62b53da422` was built and the existing Cloud Run
service successfully updated, serving 100% of traffic to the new revision.
The final probe reported `public_health_verified`, `missing_token_denied`,
`invalid_token_denied`, `assets_readonly_enabled` and `asset_token_probes_denied`
all true, with `trading_enabled: false`.

The public probe's `okx_connection_verified: false` is expected: it intentionally
does not read private amounts or run an authenticated exchange scan.

## Owner browser asset-query verification

The owner's dashboard screenshot received on **2026-10-17 09:26:37,
Asia/Taipei** shows the successful demo asset query message and a completion
timestamp of **09:25:51**. The USD equity summary and currency, position and
standard pending-order sections render through the authenticated route.

This verifies the manual owner login -> asset API -> read-only OKX adapter ->
frontend validation/rendering path for that request. No private amounts, currency
inventory, position contents, order contents or screenshot are recorded in this
public repository. It does not establish ongoing connection health, exercise
every possible populated-table case, or authorize a trade.

Automatic clearing after 30 seconds and clearing on logout have automated unit
coverage; their live owner-browser behavior has not yet been separately confirmed.

The next frontend revision adds a visible seconds-remaining indicator. It reads
only local session state and never refreshes a Firebase token or sends an API
request. Freshness is bounded by both the wall clock and a monotonic elapsed-time
deadline so moving the computer clock backward cannot extend a snapshot. The UI
clock pauses while hidden and rechecks expiry when visible again. An early
platform timeout is re-armed to the remaining deadline. These behaviors have
unit coverage; they do not imply a newly completed live browser acceptance test.

The amounts presentation revision groups thousands, shows USD amounts with two
decimal places, and shows other monetary fields with up to eight decimal places.
A leading approximation sign marks nonzero discarded digits. Rounding is decimal
half-up using integer/string operations, never binary floating-point conversion.
Contract counts stay at source precision. A checkbox inside the private panel
restores exact source strings without a request or any mutation of the snapshot.
Missing amounts remain unavailable, distinct from zero. Toggling cannot restore
expired data because it reads the expiry-checked session before rendering.

The privacy revision adds a page-memory-only "隱藏資產明細" preference that can
be selected before a query. While selected, the summary and every currency,
position and pending-order cell are removed from the DOM, including empty-state
inventory messages. The panel retains only fixed query status and timestamps.
This is display privacy, not a logout: the validated snapshot remains in session
memory until its original expiry so it can be shown again without a request.
Unhiding always checks expiry first and cannot recover an expired snapshot.
The preference is not persisted, and no balance or inventory is added to
diagnostic copying. Unit tests cover DOM removal and unhide-after-expiry.

## Contract and boundaries

`GET /api/okx/assets` uses the same Firebase owner UID and revoked-token check as
the status endpoint. It requires `SINKA_OKX_ASSETS_API=true` in addition to the
existing demo/read-only flags. Missing login, a different UID, or a revoked token
must stop before constructing the OKX client. No request accepts OKX credentials.

Each manual snapshot first requires API permission exactly `read_only`, then
reads trading-account balances, SWAP positions and bounded pending-order pages.
The status and asset endpoints share one in-process scan lock and a 30-second
scan timeout. There are no writes, order/cancel routes, workers or schedules.

The response has schema version 1, `mode=demo_readonly`, `trading_enabled=false`,
`api_permissions_readonly=true`, `okx_connection_verified=true`, `atomic=false`,
completion/expiry timestamps, and the following allowlisted data:

- `summary.total_equity_usd`: trading-account total equity in USD.
- `currencies`: currency, equity, cash/available balances, unrealized PnL, each
  amount in that currency. Blank/missing optional values become null, never zero.
- `positions`: instrument, position side, margin mode, contract count, average
  and mark prices in USDT, unrealized PnL and its currency, optional USD notional.
- `pending_orders`: instrument, buy/sell, position side, active state, requested
  and filled contract counts, optional price in USDT. No order/client IDs.

Scope is the trading account and the five monitored USDT perpetual contracts
(BTC, ETH, SOL, XRP, DOGE); it excludes the funding account and algo/conditional
orders. Nonzero positions or active standard orders outside the supported five
instruments cause an unavailable result rather than silently hiding exposure.
The UI says "未完成委託" for standard pending orders; this does not claim to list
all exchange-side conditional/algorithmic instructions.

The reads occur sequentially. The completion timestamp is a query timestamp,
not proof that all exchange fields describe exactly the same instant.
Quantities remain decimal strings; no floating-point rounding is used to
compare requested and filled quantities. Unknown fields, credentials, account
IDs, position IDs and order IDs are not returned to the browser.

Both endpoints have strict GET CORS and `Cache-Control: no-store, private`.
Private amounts live only in the authenticated page's memory and DOM. Starting
a query, failure, expiry, logout, account change or page unload removes them.
No localStorage, Firestore persistence, diagnostics export or telemetry receives
asset amounts. Diagnostic copying includes only fixed states and timestamps.

## Deployment

The existing authorized script builds the new isolated image and enables the
asset flag without changing instance limits or adding resources:

```sh
bash firebase/deploy-okx-status.sh --approved OWNER_FIREBASE_UID
```

It checks the public health capability and missing/invalid token denials on
both GET routes, without making an authenticated OKX request or printing amounts.
After deployment, the owner signs in and manually presses
"更新模擬帳戶資產". Verify a fresh result, units, empty-state messages, clearing
after 30 seconds, and clearing on logout. No private amounts need to be shared
in chat; the fixed diagnostic snapshot is enough for transport/auth failures.

The service remains min=0/max=1, one CPU, 512 MiB, concurrency 4. Retained images,
builds and request processing can incur charges. No extra IAM role is needed
beyond the existing three secret bindings and Firebase revoked-session reader.

Sources: [OKX API v5](https://www.okx.com/docs-v5/en/) account balance, account
positions and standard pending-order definitions. USD total equity must not be
labeled USDT; SWAP size/fill fields are contracts, not coin quantities.
