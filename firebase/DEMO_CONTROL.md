# OKX demo control — bounded acceptance console

This controls the existing Cloud Shell demo helper. It does not deploy a 24/7
trader, migrate the SQLite ledger, create a real-trading route, accept API keys,
or turn offline unit results into exchange acceptance.

## Components

- GitHub Pages, existing memory-only Firebase owner login: dedicated control
  origin is pinned to `sinka-okx-demo-control-361301529939.asia-east1.run.app`.
- Separate Cloud Run command API: Firebase owner UID + revoked-token validation
  on every status/command request. No OKX modules or exchange secret permissions.
- Named Firestore database `sinka-demo-control`: client rules deny all, command
  API IAM is scoped to this database. Original databases are untouched.
- One opted-in Cloud Shell runner: existing `$HOME/sinka-demo-trial/account.json`
  and `ledger.sqlite` are mandatory. Original helper remains responsible for all
  account checks, durable intent/risk state, exchange writes and reconciliation.
- Runner publishes heartbeat, sanitized results and the last 30 changing reports.
  Both offline and stale states remain visibly unverified.

## Authorized deployment (creates billable resources)

One-time combined deployment and runner connection, from the existing authorized
Cloud Shell checkout root:

```bash
bash firebase/connect-demo-control.sh --approved
```

Or deploy separately:

```bash
python3 firebase/deploy-demo-control.py --approved
```

The script reuses the existing read-only service's owner UID and custom Firebase
revocation-reader role. It creates only the dedicated control service/account,
separate database + deny-client rules, scoped IAM, and a control API build in the
existing Artifact Registry. Cloud Run scales to zero with a maximum of one.
There is no always-on VM or automatic strategy start. Billing is usage-dependent;
this script does not promise zero cost or enforce a spending cap.

It verifies process health plus missing-token denial on both command/status
routes. A failed deployment does not start trading. Review the stage shown and
Cloud Console logs, rather than pasting credentials.

## Connect the existing execution environment once

Install the new control dependency in the existing venv:

```bash
"$HOME/sinka-demo-venv/bin/python" -m pip install 'google-cloud-firestore>=2.20,<3'
```

Start an opted-in runner from the same checkout (default one hour, maximum four):

```bash
"$HOME/sinka-demo-venv/bin/python" backend/okx_demo_control_runner.py --connect --seconds 3600
```

The Cloud Shell account uses its existing Google application credentials. The
runner fetches exchange secrets only through the unchanged helper after a fixed
command is claimed. No key is entered into a browser or command mailbox.

Open the app, log in via Overview, then select the automatic strategy tab and
press Connect. The API URL is prefilled. Read-only status must say runner online
before Start is enabled. Commands: Start (15-minute demo strategy), Stop
(graceful stop/reduce), Recover (existing intent only), Audit (GET-only review),
Settle (GET-only exchange reads plus atomic ledger archive).

## Failure semantics

- Server-time bounded 30-second command expiry, runner lease and generation.
- Request UUID receipts are durable, including after later commands; accepted
  duplicates never create another operation. No arbitrary shell/URL/parameters.
- Claim is recorded before spawning. A crash in that interval is ambiguous and
  never replayed; takeover requires recovery rather than a new entry.
- Stop targets the active request, not a PID. SIGINT requests existing helper
  cleanup. A successful stop request is not proof of flatness.
- No SIGKILL. If graceful cleanup outlives the controller, the child retains the
  local flock and existing ledger lease; a second controller cannot race it.
- Losing the control lease or Firestore connection stops the controller and
  requests graceful cleanup. Offline history never implies current flatness.
- Bounded run-end reduction may require Audit then Settle; it does not count as
  price-triggered TP/SL acceptance. Existing start gates prevent bypassing it.

## Acceptance still required

Authenticated owner controls on deployed infrastructure, denial for another
owner, live heartbeat/offline transitions, a bounded demo start/stop, protected
position monitoring, natural TP and SL, automatic native-close settlement, and
restart recovery. Local API/mock tests do not prove these live checks.
