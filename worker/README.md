# Persistent paper strategy worker

Runs the existing SNR-5M-1 research strategy as an independent Node process.
This implements process-level persistence and recovery. A bounded GCP VM
trial startup has now been verified by the owner; it is not a 24-hour service,
does not make the GitHub Pages tab run while closed, and does not enable any
OKX demo/live order route. The strategy's first historical sample lost money;
[the original acceptance result](../strategy/ACCEPTANCE.md) remains unchanged.

## Run and bounded startup check

Use Node 22.13+ (or 24+) with built-in `node:sqlite`. On Node 22 this API remains
experimental; see the [official Node documentation](https://nodejs.org/download/release/latest-jod/docs/api/sqlite.html).
No npm packages, API keys, account tokens or secret access are needed.

From the repository root, keep the database **outside the public website/repo**:

```sh
node worker/paper-worker.mjs --db /var/lib/sinka-paper/paper.sqlite --symbol BTC-USDT-SWAP --once
```

The database parent must be writable by the service user. `--once` fetches a
single public snapshot, establishes the baseline and stops, without opening a
startup position. Existing positions instead follow recovery rules below.
For a bounded process check, replace `--once` with `--max-seconds 60`.
Omit both flags for ongoing paper operation on a machine that stays awake.
Allowed markets are BTC/ETH/SOL/XRP/DOGE USDT swaps, one selected market per DB.

Run one worker for each independent database. Do not share a SQLite database
across different hosts or remote/network filesystems. Using distinct databases
creates independent paper accounts; the lease does not coordinate those.

## Container

```sh
docker build -f worker/Dockerfile -t sinka-paper-worker .
docker volume create sinka-paper-data
docker run --rm --network none --entrypoint node sinka-paper-worker worker/container-smoke.mjs
docker run --name sinka-paper-worker --mount source=sinka-paper-data,target=/data sinka-paper-worker --symbol BTC-USDT-SWAP
```

The isolated image contains only the paper engine/client/worker and runs as
non-root. There is no HTTP listener, exposed port, Firebase admin library,
private OKX transport or secret mount. The smoke test injects deterministic
market data and verifies real disk persistence with networking disabled.
The final command performs public GETs and ongoing paper arithmetic until
stopped. It is documented for the operator; no ongoing worker is started by
this release. Container/VM hosting and disk must be provisioned separately.

A single managed process with a persistent local volume is required. This is
not suitable for an ordinary scale-to-zero Cloud Run service: its local disk
is ephemeral and a background loop does not establish a durable execution
service. A multi-host/cloud worker needs a shared durable ledger and distributed
lease before deployment. This release creates no cloud resource or charge.

## Durability and recovery contract

- SQLite WAL, full synchronous commits, prepared statements and atomic account
  updates. Identical accounts do not generate redundant checkpoint revisions.
- Checksummed schema/versioned checkpoints. Restore validates every closed fill,
  both fees, cash reconciliation, active position, risk sizing, daily counters
  and strategy mode/version. Corruption is an error, never a fresh 10,000 reset.
- A transactional 30-second ownership lease with five-second heartbeat fences
  duplicate processes using the **same** database. An expired owner cannot
  renew itself or write after takeover; every save also checks its revision.
  Clock rollback or a lost lease stops the worker.
- Restart restores cash, daily entry count, loss streak/halt, cooldown, last
  signal and open position. An old position is paper-flattened only after a
  fresh quote, labeled `recovery`, and no new entry is taken in that recovery
  invocation. If quotes are unavailable it stays persisted and retries while
  stopped from new entry. The operator can start the worker again once flat.
- SIGTERM/SIGINT and bounded shutdown checkpoint the existing open position;
  they do not fabricate a close. A crash is recoverable from the last committed
  checkpoint. A storage failure stops all further strategy polling/entries.
- Logs contain only mode/version, status, active/position flags, trade counts
  and revision, plus sanitized event/quote timestamps and freshness. A heartbeat
is logged every 30 seconds. No API key input, credentials, financial account data or raw
  transport errors are logged.

Exit 0 means bounded/normal stop or successful recovery flatten. Exit 2 means
invalid configuration, held/lost lease, storage/corruption, or a flat-market
failure stop. Supervisors must avoid immediate restart loops and must allow
at least the 30-second lease to expire after an ungraceful crash. Paper results
can differ through outages: fills use the next observed valid price, never an
invented historical fill. No part of this lifecycle guarantees profitability.

A separate [30-minute GCP VM trial proposal](VM_TRIAL.md) includes a default
no-op deployment plan, source integrity checks and read-only cloud verification.
Its original cost approval and startup verification are recorded there;
cloud stop/start persistence acceptance remains pending.

## Verification

```sh
node --test tests/*.test.mjs
```

Coverage includes real SQLite close/reopen, corrupted checkpoint rejection,
rollback, loss limits surviving restart, two connections contending for a
lease, an actual killed child process, expired-owner fencing, restored-position
flattening, missing quotes, write failures and the bounded worker lifecycle.
