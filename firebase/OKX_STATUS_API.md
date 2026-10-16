# Authenticated OKX demo status API candidate

**Code and local tests only; not deployed or connected to the public dashboard.**
The previously deployed one-shot smoke Job remains separate.

## Purpose and behavior

`backend/okx_status_app.py` is a separate FastAPI application. It reuses the
existing GET-only demo adapter and strict read-only API-permission smoke check.
It does not import the paper ledger, expose order routes, run background tasks,
write Firestore records, or add a schedule.

- `GET /health`: process liveness only; explicitly does not claim OKX connectivity.
- `GET /api/okx/status`: verifies a Firebase ID token, including revocation, and
  requires its UID to exactly match `SINKA_OWNER_UID` before constructing an OKX client.
- Requires `SINKA_OKX_READONLY_API=true` and `SINKA_OKX_DEMO=true`; disabled otherwise.
- Requires the existing three OKX Secret Manager-backed environment variables.
  No secret is read or accepted from a request parameter or frontend field.
- At most one active OKX scan per API process; concurrent requests get 429 and
  `Retry-After: 5`. This is not a global rate limit across multiple instances.
- Total scan timeout is 30 seconds. Timeout returns 504; connection, permission
  or validation failure returns 503. No automatic retry or cached fallback.
- Every response uses `Cache-Control: no-store, private`. CORS allows only GET
  from `https://sinkayang-bot.github.io` with an Authorization header.
- Success returns fixed endpoint checks, read-only permission evidence,
  `checked_at`, `valid_until` (30 seconds after check completion), and
  `trading_enabled: false`. It excludes UID, API credentials, account balances,
  position details, order identifiers and pending-order counts.
- Failures never return a successful freshness timestamp or raw exchange errors.
- Swagger / OpenAPI routes are disabled. The container disables access logs.

A future frontend must clear any previous verified indicator immediately after
logout, request failure, expiry, or account change. It must obtain Firebase ID
tokens from the login SDK rather than asking the user to paste a token. Store
neither bearer tokens nor private-account data in localStorage.

## Container

The isolated image uses `backend/Dockerfile.okx-status`. It copies only the
Firebase authorization helper, GET adapter, pure order parsers, smoke checks and
status application. It runs uvicorn using Cloud Run's `PORT`; credentials are
not present in the image. CI builds the image and verifies it imports with no
credentials and no network.

## Deployment prerequisites still to complete

1. Confirm Firebase Authentication is configured for project `sinka-quant`,
   enable the selected login provider and authorize the Pages domain.
2. Resolve the owner's Firebase UID through their authenticated account.
3. Prepare a dedicated service identity with only required secret references
   and Firebase revocation-check access (`firebaseauth.users.get`). Do not
   grant broad Owner / Editor roles or reuse a paper ledger writer unnecessarily.
4. Review Cloud Run ingress and application authentication together: only
   `/health` is public in application logic; all exchange diagnostics require
   owner Firebase authentication regardless of CORS.
5. Prepare a scale-to-zero deployment with bounded maximum instances,
   concurrency and request timeout, then obtain deployment/cost confirmation.
6. Verify unauthenticated, other-user, revoked-token, disabled-mode and
   successful demo-read-only cases in the deployed service before enabling a
   frontend login and refresh control.

No IAM grants, Firebase configuration changes, cloud deployments or real OKX
requests were performed while implementing this candidate.

References: [Firebase ID token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens),
[revoked-session checks](https://firebase.google.com/docs/auth/admin/manage-sessions),
[FastAPI response headers](https://fastapi.tiangolo.com/advanced/response-headers/).
