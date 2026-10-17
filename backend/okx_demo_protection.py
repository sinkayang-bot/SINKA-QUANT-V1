"""Offline assessment of demo protection. No requests or trading authority.

Supported: one live, reduce-only, fixed-size OCO with market exits.
Other configurations fail closed. Actions are diagnostic labels only.
"""
from decimal import Decimal
from okx_orders import ACTIVE, parse_order
from okx_reconcile import number
from okx_risk import SYMBOLS, positive

GROUPS = ("entry", "positions", "pending_orders", "pending_algos")


def report(reason, *actions, protected=False):
    return {"protection_verified": protected, "reason": reason,
            "new_entry_allowed": False, "submission_enabled": False,
            "actions": list(actions)}


def _fresh_groups(evidence, now_ms):
    if type(now_ms) is not int or now_ms <= 0:
        raise ValueError("Invalid assessment time")
    if not isinstance(evidence, dict) or evidence.get("demo_verified") is not True:
        raise ValueError("Verified demo account required")
    times = []
    for name in GROUPS:
        group = evidence.get(name)
        if not isinstance(group, dict) or group.get("complete") is not True:
            raise ValueError("Complete snapshots required")
        stamp = group.get("received_at_ms")
        if type(stamp) is not int or not 0 <= now_ms - stamp <= 5000:
            raise ValueError("Stale or future snapshot")
        rows = group.get("rows")
        if not isinstance(rows, list) or len(rows) > 500 or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Invalid snapshot rows")
        times.append(stamp)
    if max(times) - min(times) > 2000:
        raise ValueError("Incoherent snapshot times")


def _plan_body(plan):
    if (not isinstance(plan, dict) or plan.get("environment") != "demo"
            or plan.get("version") != 1 or plan.get("submission_enabled") is not False
            or plan.get("headers") != {"x-simulated-trading": "1"}
            or plan.get("method") != "POST" or plan.get("path") != "/api/v5/trade/order"):
        raise ValueError("Offline demo plan required")
    body = plan["body"]
    if (body["instId"] not in SYMBOLS or body["side"] not in {"buy", "sell"}
            or body["posSide"] != "net" or body["tdMode"] != "isolated"
            or body["ordType"] != "market" or len(body["attachAlgoOrds"]) != 1):
        raise ValueError("Unsupported plan")
    positive(body["sz"], "contracts")
    bracket = body["attachAlgoOrds"][0]
    if (not bracket["attachAlgoClOrdId"] or not body["clOrdId"]
            or any(bracket[k] != value for k, value in {
                "tpOrdPx": "-1", "slOrdPx": "-1",
                "tpTriggerPxType": "last", "slTriggerPxType": "last"}.items())):
        raise ValueError("Invalid protective plan")
    sl, tp = positive(bracket["slTriggerPx"], "stop"), positive(bracket["tpTriggerPx"], "target")
    if (body["side"] == "buy" and sl >= tp) or (body["side"] == "sell" and sl <= tp):
        raise ValueError("Invalid protective direction")
    return body, bracket


def assess_recovery(plan, evidence, *, now_ms):
    """Consume trusted-reader snapshots, never unlock or send an action.

    Complete means all pages of account positions/normal orders/algo types,
    not just tracked-ID results. received_at_ms is local receipt time, not an
    old order update time. Caller must bind all reads to one demo account/session.
    This is offline assessment, not an authentication boundary or executor.
    """
    try:
        body, bracket = _plan_body(plan)
        _fresh_groups(evidence, now_ms)
        entries = evidence["entry"]["rows"]
        if len(entries) != 1:
            return report("entry_result_unknown", "halt_entries", "query_entry_no_resubmit")
        raw = entries[0]
        entry = parse_order(raw)
        if (entry.client_order_id != body["clOrdId"] or entry.instrument != body["instId"]
                or entry.requested != Decimal(body["sz"])
                or any(raw.get(k) != body[k] for k in ("side", "posSide", "tdMode", "ordType"))
                or (entry.state == "filled" and entry.filled != entry.requested)
                or (entry.state == "live" and entry.filled != 0)
                or (entry.state == "partially_filled" and not 0 < entry.filled < entry.requested)):
            raise ValueError("Entry identity or fill mismatch")
        pending = evidence["pending_orders"]["rows"]
        if entry.state in ACTIVE:
            if len(pending) != 1 or parse_order(pending[0]) != entry:
                raise ValueError("Pending entry mismatch or foreign order")
            if any(pending[0].get(k) != body[k] for k in ("side", "posSide", "tdMode", "ordType")):
                raise ValueError("Pending parameters changed")
        elif pending:
            raise ValueError("Unresolved pending order")
        positions = []
        for row in evidence["positions"]["rows"]:
            size = number(row.get("pos"), allow_negative=True)
            if size:
                positions.append((row, size))
        algos = evidence["pending_algos"]["rows"]
        if not positions:
            if algos:
                return report("orphan_or_triggered_protection", "halt_entries", "reconcile_algo_children_and_cleanup")
            if entry.state in ACTIVE:
                return report("entry_still_active", "halt_entries", "cancel_remainder_then_reconcile")
            return report("flat_requires_fill_audit", "halt_entries", "audit_exit_fills_before_release")
        if len(positions) != 1:
            return report("unexpected_exposure", "halt_entries", "reconcile_all_positions")
        position, signed = positions[0]
        direction = 1 if body["side"] == "buy" else -1
        if (position.get("instId") != body["instId"] or position.get("posSide") != "net"
                or position.get("mgnMode") != "isolated" or signed * direction <= 0
                or abs(signed) != entry.filled or entry.filled <= 0):
            return report("position_fill_mismatch", "halt_entries", "reconcile_position_and_fills")
        remainder = ("cancel_remainder_then_reconcile",) if entry.state in ACTIVE else ()
        if len(algos) != 1:
            return report("missing_or_ambiguous_protection", "halt_entries", *remainder,
                          "resolve_protection_or_emergency_exit")
        algo = algos[0]
        if (algo.get("algoClOrdId") != bracket["attachAlgoClOrdId"]
                or not isinstance(algo.get("algoId"), str) or not algo["algoId"].isdigit()
                or algo.get("instId") != body["instId"]):
            return report("foreign_protection", "halt_entries", "reconcile_untracked_algo")
        if algo.get("state") != "live":
            return report("protection_not_live", "halt_entries", *remainder,
                          "reconcile_triggered_exit_no_duplicate_close")
        matches = (
            algo.get("ordType") == "oco" and algo.get("instType") == "SWAP"
            and algo.get("side") == ("sell" if direction == 1 else "buy")
            and algo.get("posSide") == "net" and algo.get("tdMode") == "isolated"
            and (algo.get("reduceOnly") is True or algo.get("reduceOnly") == "true")
            and algo.get("closeFraction", "") == ""
            and positive(algo.get("sz"), "protective size") == abs(signed)
            and all(algo.get(k) == bracket[k] for k in
                    ("tpOrdPx", "slOrdPx", "tpTriggerPxType", "slTriggerPxType"))
            and positive(algo.get("tpTriggerPx"), "target") == Decimal(bracket["tpTriggerPx"])
            and positive(algo.get("slTriggerPx"), "stop") == Decimal(bracket["slTriggerPx"]))
        if not matches:
            return report("protective_parameters_mismatch", "halt_entries", *remainder,
                          "resolve_protection_or_emergency_exit")
        if remainder:
            return report("partial_fill_protected_remainder_active", "halt_entries", *remainder,
                          protected=True)
        return report("open_position_protected", "monitor_position_and_protection", protected=True)
    except (ValueError, TypeError, KeyError, ArithmeticError, IndexError):
        return report("invalid_or_stale_evidence", "halt_entries", "refresh_and_reconcile_no_resubmit")
