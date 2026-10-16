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
successful Cloud Shell checks establish deployment evidence. The owner's browser
subsequently completed the authenticated check described below.

## Owner browser end-to-end check passed

The owner's screenshot of the deployed Pages dashboard shows Google signed in,
the manual update button enabled, and the message "本次唯讀查詢通過，交易功能關閉。"
The completion timestamp displayed is **2026-10-17 03:02:31, Asia/Taipei**.
The screenshot was received at 03:02:43 and shows the successful result within
its 30-second validity period.

This verifies the owner login -> deployed status API -> OKX demo read-only
diagnostics -> frontend validation path for that single request. The frontend
accepts success only when all five endpoint checks pass, read-only permission
is confirmed, trading is disabled and the timestamps are fresh.

The cache issue in the earlier pending-service screen was fixed by versioning
both the auth entry module and its Firebase configuration import (`f13c38e6`).
No order was submitted. There is no scheduled or continuous private-account
connection, and no private balance or position data is displayed. After 30
seconds, an expired status is expected; a new manual check is needed for fresh
evidence. The end-to-end result is screenshot evidence from the owner's browser,
not a claim that this automation browser can access the Cloud Run origin.

Earlier candidate and login documents describe their respective implementation
stages. This record supersedes their statements that no status service exists.
