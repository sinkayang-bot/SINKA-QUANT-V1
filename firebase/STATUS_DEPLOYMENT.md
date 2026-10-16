# Read-only status deployment

The owner approved this deployment's costs and setup steps on 2026-10-17
(Asia/Taipei). Google login and Firebase user creation were verified on the
deployed Pages frontend. The Cloud Run status API remains undeployed until the
script below completes successfully. This browser's Cloud Console and embedded
Cloud Shell currently display `Site Unavailable`.

Run from an authenticated Google Cloud Shell checkout:

```sh
bash firebase/deploy-okx-status.sh --approved OWNER_FIREBASE_UID
```

Use the UID shown in the `sinka-quant` Firebase Authentication user list for the
owner who just signed in. Do not use an email address, ID token or OKX API key.
The script validates the UID format before any cloud operation.

The fixed target is `sinka-quant`, `asia-east1`, service
`sinka-okx-readonly-status`, and existing registry `sinka-okx-readonly`.
It creates a dedicated service account, grants access to the three existing
OKX secrets and a custom role containing only `firebaseauth.users.get`, builds
an isolated container and deploys its immutable digest. Secret version metadata
is read, but payloads are never fetched by the script. Secret versions are pinned
for the revision. Requests require an owner Firebase token with revocation checks.

Billing settings: one CPU, 512 MiB memory, request-based CPU, minimum zero,
maximum one instance, concurrency four, 60-second HTTP timeout, no CPU boost.
There is no schedule or background worker. Scale-to-zero is not a spending cap;
builds, retained images, request processing and other cloud usage may cost money.

Cloud Run's HTTP ingress is public so GitHub Pages can reach it. The application
checks Firebase owner authorization before constructing an OKX reader. The
script checks process health and verifies both missing and invalid tokens return
401. It does not run an OKX scan. It prints only the service URL, image digest and
sanitized verification booleans. Return that final JSON for frontend configuration
and an authenticated manual GET-only demo check. A successful health probe alone
does not verify OKX connectivity.

Other-user, revoked-token, disabled-mode and sanitized success behavior have
automated backend coverage. Live owner status must still be verified after
deployment; do not describe those unit tests as deployed integration results.
