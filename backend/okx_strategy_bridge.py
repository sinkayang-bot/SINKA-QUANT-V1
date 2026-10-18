"""Offline SNR-5M-1 signal -> contract-sized demo plan. No network or orders.

Consumes the existing JS engine's signal; does not reimplement the strategy.
Sizing is stricter than the research account (10% margin cap). This is not a
replacement for lease-fenced admission, daily limits, fresh account reads or
execution-time revalidation.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from okx_demo_intent import demo_plan
from okx_instruments import instrument_spec
from okx_risk import positive

VERSION = "SNR-5M-1"
STRATEGY_PREFIXES={VERSION:'SNR5M1', 'SNR-5M-FAST-1':'SNR5MFAST1','SNR-5M-RETEST-1':'SNR5MRETEST1'}
BAR_MS = 300000


def timestamp(value):
    if type(value) is not int or not 0 <= value <= 9007199254740991:
        raise ValueError("Invalid timestamp")
    return value


def fresh(stamp, now, maximum):
    if not 0 <= now - timestamp(stamp) <= maximum:
        raise ValueError("Stale or future source")


def align(value, step, rounding):
    return (value / step).to_integral_value(rounding=rounding) * step


def plan_strategy_signal(*, account_scope, strategy_version, symbol, signal,
                         quote, instrument, equity_usdt, available_usdt,
                         now_ms, started_at_ms, instrument_received_at_ms,
                         balance_received_at_ms, leverage=3, demo=False):
    """Return a plan descriptor, never an executable/authorized action.

    All balances/metadata/quotes are caller-provided offline inputs. Timestamp
    freshness is local receipt freshness, not verified exchange authenticity.
    The runtime must bind sources to the correct account and strategy symbol.
    Startup rejects signals already confirmed when the worker started.
    """
    if demo is not True or strategy_version not in STRATEGY_PREFIXES:
        raise ValueError("Explicit demo and supported strategy required")
    if not isinstance(signal, dict) or not isinstance(quote, dict):
        raise ValueError("Signal and quote objects required")
    now = timestamp(now_ms)
    started = timestamp(started_at_ms)
    if started > now:
        raise ValueError("Invalid worker start time")
    fresh(instrument_received_at_ms, now, 60000)
    fresh(balance_received_at_ms, now, 5000)
    spec = instrument_spec(instrument)
    if spec["symbol"] != symbol or quote.get("symbol") != symbol:
        raise ValueError("Instrument, quote and strategy symbol mismatch")
    if signal.get("reason") != "signal" or type(signal.get("side")) is not int or signal["side"] not in (1, -1):
        raise ValueError("Confirmed strategy signal required")
    opened = timestamp(signal.get("ts"))
    ready = timestamp(signal.get("readyAt"))
    if opened % BAR_MS or ready != opened + BAR_MS or signal.get("id") != str(opened):
        raise ValueError("Signal candle identity mismatch")
    fresh(ready, now, 20000)
    if ready <= started:
        raise ValueError("Signal predates worker start")
    quote_time = timestamp(quote.get("ts"))
    fresh(quote_time, now, 15000)
    if quote_time < ready:
        raise ValueError("Quote predates confirmed signal")
    with localcontext() as context:
        context.prec = 60
        price = positive(quote.get("price"), "quote")
        close = positive(signal.get("close"), "signal close")
        atr = positive(signal.get("atr"), "ATR")
        raw_stop = positive(signal.get("stop"), "stop")
        if abs(price - close) > atr * Decimal("0.5"):
            raise ValueError("Entry drift exceeds half ATR")
        side = signal["side"]
        tick = spec["tick_sz"]
        # Adverse entry anchor; outward stop keeps the structural stop intact.
        anchor = align(price, tick, ROUND_CEILING if side == 1 else ROUND_FLOOR)
        if abs(anchor - close) > atr * Decimal("0.5"):
            raise ValueError("Rounded entry drift exceeds half ATR")
        stop = align(raw_stop, tick, ROUND_FLOOR if side == 1 else ROUND_CEILING)
        entry = anchor * (1 + side * Decimal("0.0002"))
        distance = side * (entry - stop)
        if anchor <= 0 or stop <= 0 or not Decimal("0.002") <= distance / entry <= Decimal("0.03"):
            raise ValueError("Rounded stop distance outside strategy limits")
        target = anchor + side * 2 * abs(anchor - stop)
        if target <= 0:
            raise ValueError("Invalid target")
        equity = positive(equity_usdt, "equity")
        available = positive(available_usdt, "available balance")
        if available > equity:
            raise ValueError("Available balance exceeds verified equity")
        lev = positive(leverage, "leverage")
        if lev != lev.to_integral_value() or lev > 3:
            raise ValueError("Leverage exceeds limit")
        stopped = stop * (1 - side * Decimal("0.0002"))
        contract = spec["ct_val"]
        loss_per_contract = contract * (abs(entry - stopped) + (entry + stopped) * Decimal("0.0005"))
        conservative_notional = contract * max(anchor, entry)
        entry_fee = contract * entry * Decimal("0.0005")
        # Exchange maximums are required, not inferred from currency quantity.
        max_market = positive(instrument.get("maxMktSz"), "maximum market contracts")
        max_stop = positive(instrument.get("maxStopSz"), "maximum stop contracts")
        caps = {
            "loss": equity * Decimal("0.005") / loss_per_contract,
            "margin": equity * Decimal("0.10") * lev / conservative_notional,
            "available": available / (conservative_notional / lev + entry_fee),
            "market": max_market, "protective": max_stop,
        }
        contracts = align(min(caps.values()), spec["lot_sz"], ROUND_FLOOR)
        if contracts <= 0 or contracts < spec["min_sz"]:
            raise ValueError("Risk budget cannot fund the minimum contract size")
        planned = demo_plan(account_scope=account_scope, signal_id=STRATEGY_PREFIXES[strategy_version]+":" + signal["id"],
            instrument=instrument, side="buy" if side == 1 else "sell",
            contracts=contracts, price=anchor, stop=stop, target=target,
            equity_usdt=equity, leverage=lev, demo=True)
        return {"plan": planned, "strategy": strategy_version, "symbol": symbol, "account_scope": account_scope,
                "signal_id": signal["id"], "signal_ready_at_ms": ready,
                "expires_at_ms": min(ready + 20000, quote_time + 15000,
                                     balance_received_at_ms + 5000, instrument_received_at_ms + 60000),
                "sizing": {"contracts": format(contracts, "f"),
                    "base_quantity": format(contracts * contract, "f"),
                    "entry_anchor": format(anchor, "f"),
                    "modeled_margin_usdt": str(contracts * conservative_notional / lev),
                    "modeled_entry_fee_usdt": str(contracts * entry_fee),
                    "modeled_loss_usdt": planned["modeled_loss_usdt"],
                    "binding_cap": min(caps, key=caps.get)},
                "submission_enabled": False}

TEST_VERSION = 'DEMO-EXECUTION-TEST-1'


def execution_test_signal_id(cycle):
    if type(cycle) is not int or not 0<=cycle<=1000000:
        raise ValueError('Verified bounded test cycle required')
    return 'once' if cycle==0 else 'cycle:'+str(cycle)


def plan_execution_test(*, account_scope, strategy_version, symbol, signal,
                        quote, instrument, equity_usdt, available_usdt,
                        now_ms, started_at_ms, instrument_received_at_ms,
                        balance_received_at_ms, leverage=3, demo=False,test_cycle=0):
    """One explicit minimum-size BTC demo connectivity test, not an SNR signal."""
    if demo is not True or strategy_version!=TEST_VERSION or symbol!='BTC-USDT-SWAP' or signal is not None:
        raise ValueError('Explicit fixed demo execution test required')
    now=timestamp(now_ms)
    if timestamp(started_at_ms)>now: raise ValueError('Invalid start time')
    fresh(instrument_received_at_ms,now,60000);fresh(balance_received_at_ms,now,5000)
    quote_time=timestamp(quote.get('ts'));fresh(quote_time,now,15000)
    if quote.get('symbol')!=symbol: raise ValueError('Quote mismatch')
    spec=instrument_spec(instrument)
    if spec['symbol']!=symbol: raise ValueError('Instrument mismatch')
    if type(leverage) is not int or not 1<=leverage<=3: raise ValueError('Verified leverage required')
    with localcontext() as context:
        context.prec=60
        equity=positive(equity_usdt,'equity');available=positive(available_usdt,'available')
        anchor=align(positive(quote.get('price'),'quote'),spec['tick_sz'],ROUND_CEILING)
        stop=align(anchor*Decimal('.99'),spec['tick_sz'],ROUND_FLOOR)
        target=anchor+2*(anchor-stop)
        qty=align(spec['min_sz'],spec['lot_sz'],ROUND_CEILING)
        signal_id=execution_test_signal_id(test_cycle)
        planned=demo_plan(account_scope=account_scope,signal_id=TEST_VERSION+':'+signal_id,
            instrument=instrument,side='buy',contracts=qty,price=anchor,stop=stop,target=target,
            equity_usdt=equity,leverage=leverage,demo=True)
        planned['purpose']='execution_test'
        notional=qty*spec['ct_val']*anchor*Decimal('1.0002')
        margin=notional/leverage;fee=notional*Decimal('.0005')
        if (notional>50 or margin>min(Decimal(10),equity*Decimal('.01'))
                or positive(planned['modeled_loss_usdt'],'loss')>min(Decimal(1),equity*Decimal('.001'))
                or margin+fee>available or qty>positive(instrument.get('maxMktSz'),'maximum')
                or qty>positive(instrument.get('maxStopSz'),'stop maximum')):
            raise ValueError('Minimum contract exceeds execution-test risk cap')
        return dict(plan=planned,strategy=TEST_VERSION,symbol=symbol,account_scope=account_scope,
            signal_id=signal_id,signal_ready_at_ms=quote_time,
            expires_at_ms=min(quote_time+15000,balance_received_at_ms+5000,instrument_received_at_ms+60000),
            sizing=dict(contracts=str(qty),base_quantity=str(qty*spec['ct_val']),entry_anchor=str(anchor),
                modeled_margin_usdt=str(margin),modeled_entry_fee_usdt=str(fee),modeled_loss_usdt=planned['modeled_loss_usdt'],binding_cap='minimum_test_size'),
            submission_enabled=False)
