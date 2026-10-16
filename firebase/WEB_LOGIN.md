# Google login and read-only status UI

The overview page uses the Firebase Web App registered in `sinka-quant`.
Google sign-in is enabled and `sinkayang-bot.github.io` is authorized.
The Firebase Web configuration contains public app identifiers, not OKX credentials.

Authentication uses the official Firebase SDK with memory-only persistence.
Reloading or closing the page clears the local login. No token is written to
storage, printed, or included in a URL. The UI does not display account identifiers.

`STATUS_API_BASE_URL` remains `null` until the dedicated status API is deployed
and reviewed. Login can work independently, but the update button stays disabled,
and no Firebase token is requested for an undeployed service. This does not
represent a live OKX connection. See [the backend contract](OKX_STATUS_API.md).

Once configured with the fixed HTTPS Cloud Run origin, the UI makes a manual
GET request to `/api/okx/status`. It accepts only the full demo read-only result
with trading disabled and all five checks passing. Evidence expires within
30 seconds. Sign-out, account changes, timeouts and unsuccessful responses clear
the verified state. There is no polling, background scan or trading request.

Validation: `node --check auth-web.mjs` and
`node --test tests/readonly-session.test.mjs` (17 tests).
