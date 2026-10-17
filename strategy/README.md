# SNR-5M-1: complete research strategy, paper execution

This module contains signal generation, position sizing, entry/exit simulation,
daily circuit breakers, net P&L, a live public-market runner and historical replay.
It has **no exchange-order adapter**, private API access, API key input or broker
credentials. The existing authenticated OKX service remains GET-only and unchanged.

## Fixed rules

- One selected BTC/ETH/SOL/XRP/DOGE USDT swap market, fixed 5m candles.
- Signal: latest confirmed candle closes above the previous 40 highs plus
  0.25 ATR14 with a bullish body and rising EMA50; bearish mirror for shorts.
- ATR is mean true range over the prior 14 candles (includes previous closes),
  not simply high-low. EMA initializes at the first close of a fixed 80-bar
  prior window, then updates with the trigger close. This finite seed is explicit
  so identical input windows give identical results in live and replay modes.
- Stop: resistance minus ATR for longs, support plus ATR for shorts.
- Entry stop distance 0.2%–3%; reject drift above 0.5 ATR from trigger close.
  Live candidates expire 20 seconds after the signal candle closes.
- Next-bar open for replay, first fresh observed quote for live paper fills.
  Start establishes a baseline; it does not trade the latest historical signal.
- 0.5% of current simulated cash at risk, including modeled entry/exit fees
  and adverse stop slippage. Maximum notional 3x current cash.
- 2R price target, maximum 60-minute holding, 15-minute cooldown after exit.
- Maximum six entries per Taiwan calendar day. Three consecutive net losses
  or a 2% marked equity decline against day-start equity halt new entries until
  the next Taiwan midnight. Floating day-start equity is marked at the first
  observed quote that day, including estimated exit fees if a position is open.
- Fee 0.05% per side and adverse slippage 0.02% per fill are **assumptions**.
  Risk limits are estimates, not guaranteed caps in gaps/outages.

## Execution and lifecycle

`snr-engine.mjs` is pure arithmetic, shared by `backtest()` and
`paper-runner.mjs`. `public-market.mjs` permits only fixed public market GETs
without cookies, redirects, auth headers or private routes. Completed candles
are parsed from OKX's `confirm=1`; malformed OHLC, duplicate timestamps and gaps
are rejected. See the official [OKX market data specification](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks).

The paper runner polls at five-second intervals, never overlaps polls, times
out after ten seconds and validates quote/candle freshness. A pause, request
failure, missing bar or >20-second polling interruption stops entries. Existing
paper exposure is closed at the next valid quote; it is never silently discarded
or closed with a fabricated stale price. Startup, pause, async completion and
page lifecycle have generation/watermark safeguards. Moving to a background tab
pauses entry and requests paper flattening; browser suspension can delay this.
The selected symbol cannot change while active, busy or holding a position.

The new **自動策略測試** page is an independent in-memory 10,000 USDT account.
Reload/new-account clears its data; CSV export preserves all closed fills.
No automatic start on load. Browser shutdown is not a continuous worker.
Existing manual paper and old backtest modules remain separate.

A separate [persistent Node paper worker](../worker/README.md) now provides
SQLite checkpoints and single-host process recovery. It is not deployed and
does not change the browser-only account lifecycle above.

## Replay assumptions and limitations

No future candles enter signals. Replay entries use the next candle open,
stop fills are adverse-slipped, and a gap beyond stop fills at the worse open.
Ambiguous same-bar stop/target touches choose stop. Target fills use the target,
never a better gap price. Replay marks daily floating-loss checks at bar closes
and checks price stops/targets against bar extremes; live uses sampled quotes.
Thus daily exits and P&L can differ. Drawdown is sampled closing equity, not
maximum intrabar drawdown. End-of-data positions close with costs and are labeled.

This is a linear-unit research model: no exchange contract lot/tick rounding,
funding, liquidation, margin tiers, order-book impact, delayed exchange fills,
partial fills or position-mode handling. It cannot be connected to an OKX
order endpoint by reusing its base-coin quantity as contract size.

Default public replay retrieves up to 1,200 confirmed 5m candles (about four
calendar days); optionally 3,000 (about ten days). Both are short research
samples, not evidence of an edge. Strategy parameters were specified before
running the sample; tests prove mechanics, not profitability. No tuning against
the acceptance sample is performed.

## Verification

```sh
node --test tests/*.test.mjs
```

Tests cover both directions, EMA/ATR and prior-range isolation, exact-window
consistency, modeled stop-risk sizing, fees/net accounting, stale/drift/duplicate
signals, holding limits, cooldown, daily resets/limits, conservative gaps and
same-bar handling, no-lookahead entry, public-only traffic, timeout, interruption,
concurrent polls, stopping during in-flight work and late responses after dispose.
