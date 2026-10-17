"""Offline bridge checks, including real JS strategy output as the input."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from decimal import Decimal
from okx_demo_intent import DemoIntentStore
from okx_strategy_bridge import plan_strategy_signal
from test_okx_demo_intent import INSTRUMENT

READY = 24300000


def args(**updates):
    return dict(account_scope="synthetic-v1", strategy_version="SNR-5M-1",
        symbol="BTC-USDT-SWAP",
        signal=dict(reason="signal", side=1, id="24000000", ts=24000000,
                    readyAt=READY, close="101", atr="0.8", stop="99.6"),
        quote=dict(symbol="BTC-USDT-SWAP", price="101", ts=READY+100),
        instrument=INSTRUMENT | dict(ctVal="0.01", maxMktSz="100000", maxStopSz="100000"),
        equity_usdt="10000", available_usdt="10000", now_ms=READY+1000,
        started_at_ms=READY-1000, instrument_received_at_ms=READY,
        balance_received_at_ms=READY, demo=True, leverage=3) | updates


def build(**updates):
    return plan_strategy_signal(**args(**updates))


class StrategyBridgeTests(unittest.TestCase):
    def test_contract_size_is_not_base_quantity(self):
        result = build()
        size = result["sizing"]
        qty = Decimal(size["contracts"])
        self.assertEqual(Decimal(size["base_quantity"]), qty * Decimal("0.01"))
        self.assertNotEqual(size["contracts"], size["base_quantity"])
        self.assertLessEqual(Decimal(size["modeled_loss_usdt"]), 50)
        self.assertLessEqual(Decimal(size["modeled_margin_usdt"]), 1000)
        self.assertEqual(qty % Decimal("0.01"), 0)
        self.assertFalse(result["submission_enabled"])

    def test_margin_available_and_exchange_caps(self):
        self.assertEqual(build()["sizing"]["binding_cap"], "margin")
        small = build(available_usdt="3")
        self.assertEqual(small["sizing"]["binding_cap"], "available")
        self.assertLessEqual(Decimal(small["sizing"]["modeled_margin_usdt"])
            + Decimal(small["sizing"]["modeled_entry_fee_usdt"]), 3)
        for field in ("maxMktSz", "maxStopSz"):
            instrument = args()["instrument"] | {field: "1.237"}
            self.assertEqual(build(instrument=instrument)["sizing"]["contracts"], "1.23")
            instrument[field] = ""
            with self.assertRaises(ValueError): build(instrument=instrument)

    def test_risk_cap_is_binding_with_wide_stop(self):
        a = args()
        a["signal"]["stop"] = "98.2"
        result = plan_strategy_signal(**a)
        self.assertEqual(result["sizing"]["binding_cap"], "loss")
        self.assertLessEqual(Decimal(result["sizing"]["modeled_loss_usdt"]), 50)

    def test_tick_rounding_is_adverse_and_stop_outward_both_sides(self):
        a = args()
        a["quote"]["price"] = "101.01"
        a["signal"]["stop"] = "99.69"
        long = plan_strategy_signal(**a)
        self.assertEqual(long["sizing"]["entry_anchor"], "101.1")
        self.assertEqual(long["plan"]["body"]["attachAlgoOrds"][0]["slTriggerPx"], "99.6")
        a = args()
        a["signal"].update(side=-1, stop="102.31")
        a["quote"]["price"] = "100.99"
        short = plan_strategy_signal(**a)
        self.assertEqual(short["sizing"]["entry_anchor"], "100.9")
        bracket = short["plan"]["body"]["attachAlgoOrds"][0]
        self.assertEqual(bracket["slTriggerPx"], "102.4")
        self.assertEqual(Decimal(bracket["tpTriggerPx"]), Decimal("97.9"))

    def test_no_upward_rounding_to_minimum(self):
        for changes in (dict(available_usdt="0.00001"), dict(equity_usdt="0.001", available_usdt="0.001"),
                        dict(instrument=args()["instrument"] | dict(minSz="1000000"))):
            with self.subTest(changes=changes), self.assertRaises(ValueError): build(**changes)

    def test_freshness_identity_drift_and_stop_guards(self):
        cases = [dict(demo=False), dict(demo=1), dict(strategy_version="other"),
            dict(started_at_ms=READY), dict(started_at_ms=READY+2000),
            dict(balance_received_at_ms=READY-5000), dict(instrument_received_at_ms=READY-60000),
            dict(now_ms=READY+20001), dict(now_ms=True), dict(leverage=4),
            dict(available_usdt="10001"), dict(equity_usdt="NaN")]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError): build(**change)
        for key, value in dict(reason="no_signal", side=True, id="wrong", ts=24000001,
                               readyAt=READY-1, stop="101", atr="NaN").items():
            a = args(); a["signal"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): plan_strategy_signal(**a)
        for key, value in dict(symbol="ETH-USDT-SWAP", ts=READY-1, price="103").items():
            a = args(); a["quote"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): plan_strategy_signal(**a)
        with self.assertRaises(ValueError):
            build(quote=dict(symbol="BTC-USDT-SWAP", price="101", ts=READY+1001))

    def test_malformed_inputs_and_coarse_tick_drift_are_rejected(self):
        for change in (dict(signal=None), dict(quote=[])):
            with self.assertRaises(ValueError): build(**change)
        a = args(instrument=args()["instrument"] | dict(tickSz="1"))
        a["quote"]["price"] = "101.01"
        with self.assertRaisesRegex(ValueError, "Rounded entry drift"):
            plan_strategy_signal(**a)

    def test_stale_quote_with_fresh_signal_and_future_metadata(self):
        with self.assertRaises(ValueError):
            build(now_ms=READY+16000, quote=dict(symbol="BTC-USDT-SWAP", price="101", ts=READY))
        with self.assertRaises(ValueError): build(instrument_received_at_ms=READY+1001)
        with self.assertRaises(ValueError): build(balance_received_at_ms=READY+1001)

    def test_five_symbols_use_matching_metadata(self):
        for base in ("BTC", "ETH", "SOL", "XRP", "DOGE"):
            symbol = base + "-USDT-SWAP"
            a = args(symbol=symbol)
            a["instrument"].update(instId=symbol, ctValCcy=base)
            a["quote"]["symbol"] = symbol
            with self.subTest(symbol=symbol):
                self.assertEqual(plan_strategy_signal(**a)["plan"]["body"]["instId"], symbol)
        with self.assertRaises(ValueError): build(symbol="ETH-USDT-SWAP")

    def test_plan_survives_journal_restart_and_duplicate_dispatch_is_fenced(self):
        with tempfile.TemporaryDirectory() as temp:
            result = build()
            path = Path(temp) / "intent.sqlite"
            store = DemoIntentStore(path)
            key = store.register(result["plan"])
            store.reserve_dispatch(key)
            store.close()
            store = DemoIntentStore(path)
            try:
                self.assertEqual(store.register(build()["plan"]), key)
                with self.assertRaises(ValueError): store.reserve_dispatch(key)
            finally: store.close()

    def test_actual_js_engine_outputs_feed_the_bridge(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for strategy contract integration acceptance")
        script = '''
        import {analyze} from './strategy/snr-engine.mjs';
        const prior=Array.from({length:80},(_,i)=>({ts:i*300000,o:100,h:100.4,l:99.6,c:100}));
        const triggers=[{o:100,h:101.2,l:99.9,c:101},{o:100,h:100.1,l:98.8,c:99}];
        console.log(JSON.stringify(triggers.map(t=>analyze([...prior,{ts:24000000,...t}]))));
        '''
        run = subprocess.run([node, '--input-type=module', '-e', script],
            cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, check=True, timeout=10)
        signals = json.loads(run.stdout)
        self.assertEqual([s["side"] for s in signals], [1, -1])
        for signal in signals:
            a = args(signal=signal)
            a["quote"]["price"] = signal["close"]
            result = plan_strategy_signal(**a)
            self.assertFalse(result["submission_enabled"])
            self.assertEqual(result["signal_id"], signal["id"])
            self.assertEqual(result["plan"]["body"]["side"], "buy" if signal["side"] == 1 else "sell")
            self.assertLessEqual(Decimal(result["sizing"]["modeled_loss_usdt"]), 50)


if __name__ == "__main__": unittest.main()
