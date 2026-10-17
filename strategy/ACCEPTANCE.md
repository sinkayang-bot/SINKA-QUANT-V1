# SNR-5M-1 acceptance — 2026-10-17

Implementation: `188421c564009ec1dc817a850642934ff00ab385`.

## Functional result: pass for paper research

- 83 Node tests passed, including existing owner/asset privacy regressions.
- Both GitHub Pages deployment workflows and frontend tests passed.
- Published strategy page loaded and the complete-strategy replay completed.
- Paper runner started with fresh public ticker data, displayed waiting for a
  new confirmed breakout, and stopped correctly on the pause action. The live
  acceptance did not open any paper position. Entry/exit execution and risk
  controls were exercised with deterministic synthetic prices in unit tests.
- CSV export produced 13 complete fill rows; summing their unrounded net P&L
  matched the displayed rounded net P&L. No manual data edits were made.
- No OKX order endpoint, authenticated account request, key input, new cloud
  service or continuous deployed worker was used for this acceptance.

## Preliminary performance result: fail

Unchanged fixed rules; public BTC-USDT-SWAP historical 5m candles:

| Item | Result |
| --- | --- |
| First candle open, Taiwan | 2026-10-13 08:50 |
| Last candle open, Taiwan | 2026-10-17 12:40 |
| Confirmed contiguous candles | 1,199 |
| Starting paper equity | 10,000 USDT |
| Closed trades | 13 |
| Winning trades | 0 |
| Net P&L, including modeled fees/slippage | -630.85 USDT |
| Sampled closing-equity maximum drawdown | 6.31% |
| Profit factor | 0.00 |

All 13 exits were stops. The approximately 0.5% modeled losses reconcile with
risk sizing and the three-consecutive-loss daily halt. The source data includes
only a short sample and says nothing conclusive about future performance, but
this observed sample provides **no evidence for promoting the strategy to
exchange execution**. The parameters were not tuned after observing the result.

This release completes the strategy's paper mechanics, not a proven profitable
system or OKX demo auto-execution. The original no-exchange-trades restriction
remains in force. Future exchange integration needs separate authorization,
contract sizing, native protective orders, fill/position reconciliation and a
persistent single-worker runtime; none can be inferred from a paper test pass.
