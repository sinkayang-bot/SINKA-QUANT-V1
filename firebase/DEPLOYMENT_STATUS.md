# SINKA QUANT deployment status

Verified on **2026-10-17, Asia/Taipei** from the operator's Cloud Shell screenshots.
This records one completed execution; it is not a live health check or a claim of continuous availability.

| Item | Verified result |
| --- | --- |
| Google Cloud project | `sinka-quant` |
| Region | `asia-east1` |
| Artifact Registry | Docker Standard repository `sinka-okx-readonly` created |
| Cloud Run Job | `sinka-okx-readonly-smoke` deployed |
| Service identity | `sinka-okx-readonly@sinka-quant.iam.gserviceaccount.com` |
| Build ID | `d5b14dc9-aedc-455c-af7f-7e84fe9665f4` |
| Source version used for the release | `93307928dc4ea2ca9352602126d31bb772c61589` |
| Immutable image | `asia-east1-docker.pkg.dev/sinka-quant/sinka-okx-readonly/okx-smoke@sha256:7dae6476a96aa355b87b5ad015914dcfb7c23609033f5899eb1b0b6c83d44a30` |
| First verified execution | `sinka-okx-readonly-smoke-rwkn7` |
| Deployment preflight | `metadata_ready: true`, `job_configuration_verified: true` |
| Execution report | Identity, immutable execution configuration, completion and matching smoke result all passed |
| Connection result | `okx_connection_verified: true`, mode `demo_readonly` |
| API permission evidence | Strict `read_only` permission and five GET endpoint checks required by this release's smoke/report code |
| Trading | `trading_enabled: false`; no order submission performed |

The code uses Secret Manager references for `OKX_API_KEY`, `OKX_API_SECRET`, and
`OKX_API_PASSPHRASE`. No secret payload, account balance, account identifier,
position detail or order identifier is recorded here.

## Current behavior

- The Job performs a single read-only check and exits. No automatic schedule was configured in this deployment.
- Closing Cloud Shell does not delete the Job or retained image. Image storage may continue to incur charges.
- The public dashboard uses OKX public market data. Its private-account figures are not connected to this Job.
- A historical success does not establish current connection health, ongoing synchronization, strategy profitability, or authorization to trade.
- The paper API and continuous worker were not deployed by this procedure.

## Next integration boundary

A live private-account dashboard would require a separately deployed authenticated
read-only backend, owner-only authorization, sanitized status storage and freshness
reporting. This release does not add that service or broaden IAM permissions.
Keep all OKX credentials in Secret Manager, never in GitHub Pages or frontend fields.

For reproducible metadata and execution checks, see [the smoke runbook](OKX_SMOKE_CLOUD_RUN_JOB.md).
