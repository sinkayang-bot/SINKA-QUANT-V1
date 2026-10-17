"""Synthetic demo lifecycle only; no network, credentials or exchange writes."""
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from okx_demo_intent import demo_plan, DemoIntentStore

INSTRUMENT = dict(instId="BTC-USDT-SWAP", instType="SWAP", state="live",
                  ctType="linear", settleCcy="USDT", ctValCcy="BTC",
                  ctVal="0.01", lotSz="0.01", minSz="0.01", tickSz="0.1")


def plan(**overrides):
    return demo_plan(**(dict(account_scope="synthetic-account-v1", signal_id="5m:1000",
        instrument=INSTRUMENT, side="buy", contracts="1", price="60000",
        stop="59400", target="61200", equity_usdt="10000", leverage=3,
        demo=True) | overrides))


def order(p, **overrides):
    return dict(ordId="12345", clOrdId=p["body"]["clOrdId"],
        instId="BTC-USDT-SWAP", sz="1", accFillSz="0", state="live",
        side="buy", posSide="net", tdMode="isolated", ordType="market") | overrides


class DemoIntentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "intent.sqlite"

    def store(self):
        store = DemoIntentStore(self.path)
        self.addCleanup(store.close)
        return store

    def test_plan_demo_only_with_native_bracket(self):
        p = plan()
        self.assertFalse(p["submission_enabled"])
        self.assertEqual(p["headers"], {"x-simulated-trading": "1"})
        self.assertLessEqual(len(p["body"]["clOrdId"]), 32)
        bracket = p["body"]["attachAlgoOrds"][0]
        self.assertEqual(bracket["slOrdPx"], "-1")
        self.assertEqual(bracket["tpOrdPx"], "-1")
        self.assertEqual(p, plan())
        for other in (plan(account_scope="other"), plan(signal_id="5m:2000")):
            self.assertNotEqual(p["body"]["clOrdId"], other["body"]["clOrdId"])

    def test_invalid_plans_rejected(self):
        for kwargs in (dict(demo=False), dict(demo=1), dict(side="hold"),
            dict(target="61200.01"), dict(target="61000"), dict(stop="60600"),
            dict(contracts="1.001"), dict(leverage=4), dict(account_scope=""),
            dict(equity_usdt="NaN"), dict(instrument=INSTRUMENT | {"ctType": "inverse"})):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                plan(**kwargs)

    def test_short_bracket_and_fee_inclusive_risk(self):
        self.assertEqual(plan(side="sell", stop="60600", target="58800")["body"]["side"], "sell")
        # Price-only loss is 46 < 50; fees and slippage push it above 50.
        with self.assertRaisesRegex(ValueError, "Fee-inclusive"):
            plan(contracts="5", stop="59080", target="61840")

    def test_persistent_crash_timeout_never_allows_retry(self):
        p = plan()
        first = DemoIntentStore(self.path)
        key = first.register(p)
        first.reserve_dispatch(key)
        first.close()  # crash after reservation, before receipt
        recovered = self.store()
        self.assertEqual(recovered.register(p), key)
        self.assertEqual(recovered.status(key)["next_action"], "query_by_client_id_no_resubmit")
        with self.assertRaisesRegex(ValueError, "never retry"):
            recovered.reserve_dispatch(key)
        with self.assertRaisesRegex(ValueError, "Existing intent"):
            recovered.register(plan(signal_id="next"))

    def test_same_signal_cannot_change_parameters(self):
        store = self.store()
        store.register(plan())
        with self.assertRaisesRegex(ValueError, "different plan"):
            store.register(plan(contracts="0.5"))

    def test_two_workers_cannot_reserve_same_intent(self):
        a, b = self.store(), self.store()
        key = a.register(plan())
        a.reserve_dispatch(key)
        with self.assertRaises(ValueError):
            b.reserve_dispatch(key)

    def test_partial_cancellation_still_requires_protection(self):
        p, store = plan(), self.store()
        key = store.register(p)
        store.reserve_dispatch(key)
        store.observe(key, order(p, state="partially_filled", accFillSz="0.4"))
        store.observe(key, order(p, state="canceled", accFillSz="0.4"))
        status = store.status(key)
        self.assertEqual(status["next_action"], "halt_entries_verify_position_and_native_protection")
        self.assertFalse(status["new_entry_allowed"])
        with self.assertRaises(ValueError):
            store.observe(key, order(p, state="live"))
        with self.assertRaises(ValueError):
            store.register(plan(signal_id="next"))

    def test_foreign_and_inconsistent_snapshots_rejected(self):
        p, store = plan(), self.store()
        key = store.register(p)
        store.reserve_dispatch(key)
        for changes in (dict(clOrdId="foreign"), dict(ordType="limit"), dict(side="sell"),
            dict(sz="2"), dict(tdMode="cross"), dict(posSide="long"),
            dict(instId="ETH-USDT-SWAP"), dict(state="filled", accFillSz="0.5"),
            dict(state="partially_filled", accFillSz="0"), dict(accFillSz="0.1")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                store.observe(key, order(p, **changes))
        self.assertEqual(store.status(key)["next_action"], "query_by_client_id_no_resubmit")

    def test_full_fill_is_not_proof_of_protection(self):
        p, store = plan(), self.store()
        key = store.register(p)
        with self.assertRaises(ValueError):
            store.observe(key, order(p))
        store.reserve_dispatch(key)
        store.observe(key, order(p, state="filled", accFillSz="1"))
        self.assertIn("verify_position_and_native_protection", store.status(key)["next_action"])

    def test_zero_fill_cancel_does_not_unlock(self):
        p, store = plan(), self.store()
        key = store.register(p)
        store.reserve_dispatch(key)
        store.observe(key, order(p, state="canceled"))
        self.assertEqual(store.status(key)["next_action"], "verify_flat_and_cleanup_before_any_new_intent")
        with self.assertRaises(ValueError):
            store.register(plan(signal_id="next"))

    def test_real_environment_envelope_rejected(self):
        store = self.store()
        p = deepcopy(plan())
        p["headers"] = {}
        with self.assertRaises(ValueError):
            store.register(p)


if __name__ == "__main__":
    unittest.main()
