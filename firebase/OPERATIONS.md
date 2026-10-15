# SINKA QUANT — Always-on operations checklist

## Why Cloud Run can stay active

With minimum instances 1 and CPU allocated for the instance lifetime (`--no-cpu-throttling`), Cloud Run can run a background worker even when no HTTP request arrives. It is **not** a guarantee of a single uninterrupted process: Cloud Run can restart or replace instances, and connections can drop.

Use a persistent Firestore-backed state machine, idempotent execution, and a lease/fencing token so old and new instances cannot process the same position simultaneously. A max-instances setting of 1 is not a substitute for distributed coordination.

## Deployment guard

`firebase/deploy-cloud-run.sh` is intentionally blocked until `backend/firestore_ready.marker` exists. Do not create that marker until:

- Firestore atomic transactions replace all SQLite account and position writes.
- Firebase Authentication owner verification is tested; no shared token in public frontend.
- A lease or idempotency mechanism prevents duplicate paper executions after restarts.
- Monitor heartbeats and failure alerts are operational.
- Recovery after simulated restarts and OKX disconnections is tested.
- Billing budget alerts and a Firestore backup/export plan are configured.

## Google Cloud prerequisites

- Dedicated Google Cloud / Firebase project, preferably separate from EPC.
- Billing enabled; Cloud Run, Firestore, Secret Manager, Cloud Build and monitoring may incur charges.
- APIs: run.googleapis.com, cloudbuild.googleapis.com, artifactregistry.googleapis.com, firestore.googleapis.com, secretmanager.googleapis.com.
- A least-privilege service account for Firestore access.
- Deploy permissions and billing changes must be authorized by the project owner.

## Operational cautions

- This is a **paper trading system**, not live OKX trading.
- Market data may disconnect or become stale. Never fill a paper stop at the requested stop price if the next observed price has already gapped past it.
- Even with always-allocated CPU, Cloud Run may restart; maintain an external heartbeat and alerts.
- Don't place OKX API keys or Google service-account JSON in GitHub, client-side code, or public URLs.
