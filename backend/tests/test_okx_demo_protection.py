"""Synthetic protection snapshots; no account connection or trading."""
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from test_okx_demo_intent import plan, order
from okx_demo_intent import DemoIntentStore
from okx_demo_protection import assess_recovery

NOW = 100000


def evidence(p, *, side="buy", fill="1", state="filled"):
    bracket = p["body"]["attachAlgoOrds"][0]
    entry = order(p, state=state, accFillSz=fill, side=side)
    algo = {k: v for k, v in bracket.items() if k != "attachAlgoClOrdId"}
    algo.update(algoClOrdId=bracket["attachAlgoClOrdId"], algoId="5678",
        instId="BTC-USDT-SWAP", instType="SWAP", tdMode="isolated", posSide="net",
        side="sell" if side == "buy" else "buy", reduceOnly="true", ordType="oco",
        state="live", sz=fill, closeFraction="")
    position = dict(instId="BTC-USDT-SWAP", posSide="net", mgnMode="isolated",
                    pos=fill if side == "buy" else "-" + fill)
    groups = dict(entry=[entry], positions=[position],
                  pending_orders=[entry] if state in ("live", "partially_filled") else [],
                  pending_algos=[algo])
    return dict(demo_verified=True, **{name: dict(complete=True,
        received_at_ms=NOW-100, rows=rows) for name, rows in groups.items()})


class DemoProtectionTests(unittest.TestCase):
    def test_invalid_evidence_reports_fixed_stage_and_field_names_only(self):
        p=plan(); e=evidence(p)
        e['entry']['rows'][0]['tdMode']='cross'
        result=assess_recovery(p,e,now_ms=NOW)
        self.assertEqual(result['diagnostics'],dict(stage='entry_validation',parameter_mismatches=['tdMode']))
        self.assertFalse(result['protection_verified'])
        e=evidence(p); e['pending_algos']['rows'][0]['sz']=''
        result=assess_recovery(p,e,now_ms=NOW)
        self.assertEqual(result['diagnostics'],dict(stage='protection_parameters_validation',missing_fields=['sz']))
        self.assertFalse(result['protection_verified'])
        e=evidence(p)
        result=assess_recovery(p,e,now_ms=NOW+10000)
        self.assertEqual(result['diagnostics'],dict(stage='snapshot_freshness',stale_groups=['entry','positions','pending_orders','pending_algos']))

    def check(self, p, e):
        result = assess_recovery(p, e, now_ms=NOW)
        self.assertFalse(result["submission_enabled"])
        self.assertFalse(result["new_entry_allowed"])
        return result

    def test_long_and_short_fully_protected(self):
        for side, stop, target in (("buy", "59400", "61200"), ("sell", "60600", "58800")):
            p = plan(side=side, stop=stop, target=target)
            with self.subTest(side=side):
                result = self.check(p, evidence(p, side=side))
                self.assertTrue(result["protection_verified"])
                self.assertEqual(result["reason"], "open_position_protected")

    def test_partial_fill_requires_cancel_and_recheck_even_when_protected(self):
        p = plan()
        e = evidence(p, fill="0.4", state="partially_filled")
        result = self.check(p, e)
        self.assertTrue(result["protection_verified"])
        self.assertIn("cancel_remainder_then_reconcile", result["actions"])
        e["pending_algos"]["rows"] = []
        result = self.check(p, e)
        self.assertFalse(result["protection_verified"])
        self.assertIn("resolve_protection_or_emergency_exit", result["actions"])

    def test_canceled_partial_fill_still_needs_full_protection(self):
        p = plan()
        e = evidence(p, fill="0.4", state="canceled")
        self.assertTrue(self.check(p, e)["protection_verified"])
        e["pending_algos"]["rows"][0]["sz"] = "0.3"
        self.assertFalse(self.check(p, e)["protection_verified"])

    def test_missing_duplicate_foreign_and_oversized_protection_rejected(self):
        p = plan()
        for change in ("missing", "duplicate", "foreign", "oversize"):
            e = evidence(p)
            rows = e["pending_algos"]["rows"]
            if change == "missing": rows.clear()
            if change == "duplicate": rows.append(deepcopy(rows[0]))
            if change == "foreign": rows[0]["algoClOrdId"] = "other"
            if change == "oversize": rows[0]["sz"] = "2"
            with self.subTest(change=change):
                self.assertFalse(self.check(p, e)["protection_verified"])

    def test_all_protective_parameters_are_checked(self):
        p = plan()
        for key, value in dict(side="buy", posSide="long", tdMode="cross", reduceOnly="false",
            ordType="conditional", instType="SPOT", sz="0.9", tpTriggerPx="61201",
            slTriggerPx="59399", tpOrdPx="61200", slOrdPx="59400",
            tpTriggerPxType="mark", slTriggerPxType="index", closeFraction="1").items():
            e = evidence(p)
            e["pending_algos"]["rows"][0][key] = value
            with self.subTest(key=key):
                self.assertFalse(self.check(p, e)["protection_verified"])
        e = evidence(p)
        e["pending_algos"]["rows"][0]["reduceOnly"] = 1
        self.assertFalse(self.check(p, e)["protection_verified"])

    def test_triggered_and_failed_algos_require_lookup_not_duplicate_exit(self):
        p = plan()
        for state in ("effective", "partially_effective", "canceled", "order_failed", "unknown"):
            e = evidence(p)
            e["pending_algos"]["rows"][0]["state"] = state
            result = self.check(p, e)
            self.assertFalse(result["protection_verified"])
            self.assertIn("reconcile_triggered_exit_no_duplicate_close", result["actions"])

    def test_each_snapshot_must_be_fresh_complete_and_valid(self):
        p = plan()
        for group in ("entry", "positions", "pending_orders", "pending_algos"):
            for key, value in (("complete", False), ("complete", "true"),
                ("received_at_ms", NOW-5001), ("received_at_ms", NOW+1),
                ("received_at_ms", True), ("rows", [None]), ("rows", {})):
                e = evidence(p)
                e[group][key] = value
                with self.subTest(group=group, key=key, value=value):
                    self.assertEqual(self.check(p, e)["reason"], "invalid_or_stale_evidence")
        e = evidence(p)
        e["entry"]["received_at_ms"] = NOW-3000
        self.assertFalse(self.check(p, e)["protection_verified"])

    def test_wrong_account_or_live_plan_never_passes(self):
        p, e = plan(), evidence(plan())
        e["demo_verified"] = False
        self.assertFalse(self.check(p, e)["protection_verified"])
        e = evidence(p)
        p["headers"] = {}
        self.assertFalse(self.check(p, e)["protection_verified"])

    def test_position_direction_quantity_mode_and_foreign_exposure(self):
        p = plan()
        for key, value in dict(pos="-1", instId="ETH-USDT-SWAP", posSide="long", mgnMode="cross").items():
            e = evidence(p)
            e["positions"]["rows"][0][key] = value
            self.assertFalse(self.check(p, e)["protection_verified"])
        for pos in ("0.5", "NaN", "Infinity"):
            e = evidence(p)
            e["positions"]["rows"][0]["pos"] = pos
            self.assertFalse(self.check(p, e)["protection_verified"])
        e = evidence(p)
        e["positions"]["rows"].append(dict(instId="ETH-USDT-SWAP", pos="1"))
        self.assertFalse(self.check(p, e)["protection_verified"])

    def test_flat_requires_fill_audit_and_orphan_cleanup(self):
        p, e = plan(), evidence(plan())
        e["positions"]["rows"] = []
        self.assertEqual(self.check(p, e)["reason"], "orphan_or_triggered_protection")
        e["pending_algos"]["rows"] = []
        self.assertEqual(self.check(p, e)["reason"], "flat_requires_fill_audit")

    def test_main_order_unknown_foreign_or_inconsistent_never_passes(self):
        p = plan()
        for key, value in dict(clOrdId="other", sz="2", side="sell", state="unknown", accFillSz="0.5").items():
            e = evidence(p)
            e["entry"]["rows"][0][key] = value
            self.assertFalse(self.check(p, e)["protection_verified"])
        e = evidence(p)
        e["entry"]["rows"] = []
        self.assertEqual(self.check(p, e)["reason"], "entry_result_unknown")

    def test_foreign_pending_order_and_missing_entry_remainder_rejected(self):
        p = plan()
        e = evidence(p)
        e["pending_orders"]["rows"] = [order(p)]
        self.assertFalse(self.check(p, e)["protection_verified"])
        e = evidence(p, fill="0.4", state="partially_filled")
        e["pending_orders"]["rows"] = []
        self.assertFalse(self.check(p, e)["protection_verified"])

    def test_unfilled_active_entry_requires_cancel_and_reconcile(self):
        p = plan()
        e = evidence(p, fill="0", state="live")
        e["pending_algos"]["rows"] = []
        e["positions"]["rows"] = []
        self.assertEqual(self.check(p, e)["reason"], "entry_still_active")

    def test_durable_intent_must_match_evidence_and_never_unlocks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "intent.sqlite"
            p = plan()
            store = DemoIntentStore(path)
            key = store.register(p)
            store.reserve_dispatch(key)
            e = evidence(p)
            store.observe(key, e["entry"]["rows"][0])
            store.close()
            store = DemoIntentStore(path)
            try:
                result = store.protection_report(key, e, now_ms=NOW)
                self.assertTrue(result["protection_verified"])
                self.assertFalse(store.status(key)["new_entry_allowed"])
                e["entry"]["rows"][0]["ordId"] = "9999"
                self.assertFalse(store.protection_report(key, e, now_ms=NOW)["protection_verified"])
                with self.assertRaises(ValueError):
                    store.reserve_dispatch(key)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
