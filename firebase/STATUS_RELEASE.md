# Status API deployment evidence

Verified from the owner's Cloud Shell screenshot on 2026-10-17 (Asia/Taipei):

- Project `sinka-quant`, region `asia-east1`.
- Dedicated identity, Firebase revocation-reader role and three secret bindings created.
- Isolated image built and service `sinka-okx-readonly-status` deployed successfully.
- Cloud Run reported 100% traffic to the deployed revision.
- Service URL: `https://sinka-okx-readonly-status-361301529939.asia-east1.run.app`.
- Script reported `public_health_verified: true`, `missing_token_denied: true`,
  `invalid_token_denied: true`, `trading_enabled: false`.

The script deliberately does not run an OKX scan. Its
`okx_connection_verified: false` is the public health probe's expected result.
It does not establish an exchange failure or a successful authenticated scan.

The frontend now targets this fixed service origin. An authenticated owner must
press the manual update button to run a GET-only demo connection check. The UI
only displays success for a complete, fresh, sanitized read-only result.

This automation browser cannot navigate the service URL (`ERR_BLOCKED_BY_CLIENT`).
That client restriction does not establish a backend outage. The screenshot's
successful Cloud Shell checks are the available deployment evidence; a live
authenticated browser request still needs verification.

Earlier candidate and login documents describe their respective implementation
stages. This record supersedes their statements that no status service exists.
