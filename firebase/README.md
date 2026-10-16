# SINKA QUANT — Firebase / Google Cloud deployment plan

The existing `backend/app.py` is a **prototype** with SQLite and a background polling task. **Do not deploy it unchanged to Cloud Run**: Cloud Run instances may stop when idle; their local filesystems are ephemeral, and the in-process monitor is not a durable scheduler.

## Target architecture

- Firebase Authentication: user sign-in; backend validates Firebase ID tokens and enforces an owner UID.
- Cloud Firestore: authoritative account balances, positions, trade fills, idempotency keys, and monitor lease/state. Use Firestore transactions to avoid double execution.
- Cloud Run: stateless API for account state, paper orders, and reconciliation. No OKX private trading credentials.
- Cloud Scheduler: invokes an authenticated Cloud Run monitoring endpoint at the supported minimum interval (typically once per minute). **This cannot guarantee timely stop execution**.
- Secret Manager: credentials and configuration; never put secrets in GitHub Pages, Git commits, or client-side JavaScript.
- GitHub Pages: existing public dashboard. Use Firebase Auth tokens, not the prototype's shared bearer token.

## Required migration before deploying

1. Replace SQLite with Firestore and model account/position/fill updates as atomic transactions.
2. Replace the long-running `monitor()` task with a single idempotent HTTP monitoring job invoked by Cloud Scheduler using service-account authentication.
3. Handle stale OKX quotes, duplicate Scheduler deliveries, downtime, partial failures, and monitoring gaps. Explicitly show last successful monitor timestamp and any outage.
4. Implement owner-only authorization and configure strict CORS for `https://sinkayang-bot.github.io`.
5. Deploy to a user-controlled Firebase/GCP project with billing enabled as required by Cloud Run/Scheduler/Firestore. Set budgets and alerts.
6. Add integration tests with Firestore emulator and mock OKX prices before enabling any automated paper orders.

## Important limitations

- Cloud Scheduler's cadence is not an exchange-native stop-loss. Simulated fills can be delayed, skipped during outages, or gap through stops.
- This plan is **paper trading only**. It cannot place real OKX orders.
- The current cloud connection panel is for the older shared-token prototype; it must be replaced by Firebase Authentication before production exposure.
- Project `sinka-quant` now has an Artifact Registry image and a one-shot demo-only read-only Cloud Run Job. Its first connection test passed on 2026-10-17 (Asia/Taipei). The paper API, continuous account synchronization and scheduler are not verified as deployed. See [deployment status](DEPLOYMENT_STATUS.md).
