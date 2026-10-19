# Demo control live acceptance — 2026-10-19 (Asia/Taipei)

Verified through the deployed GitHub Pages UI and the owner-protected control
API. The Cloud Shell runner retained the existing durable exchange ledger.

Passed:

- Public health returned HTTP 200, `mode=okx_demo_control`, live trading false.
- Unauthenticated status returned HTTP 401.
- Google owner login completed and authenticated status returned successfully.
- Fresh runner heartbeat: online, idle, no pending command, no recovery required.
- Start button produced a claimed command, busy=true, then `waiting_for_signal`.
- Actual scanner reported universe=20, eligible=16, checked=16, fetch_failed=0,
  signals=0. This was exchange demo strategy execution, not the browser paper
  simulator.
- Stop button produced worker `stop_reason=stop_requested` and final
  `phase=stopped_no_active_intent`, busy=false, no pending command, no recovery
  required. Final worker counts: five observed ticks (four waiting, one stop).
- Runner remained online after the stopped child exited, allowing another
  explicitly authorized bounded command without another terminal invocation.

No new entry occurred in this short control test. It does NOT prove position
monitoring, natural TP/SL trigger, automatic settlement or restart-with-position
acceptance. The one-hour Cloud Shell runner is still a bounded manual runtime,
not 24/7 hosting. Current stop state was observed at approximately 12:41 Taiwan.
