"""Bounded two-pass demo account reads. No writes or trading authorization.

Recent fills are diagnostics, not complete audited history. Consequently the
returned fill_audit_ready flag is ALWAYS false until a separate audit is wired.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal
import re

from okx_demo_reads import ALGO_TYPES
from okx_demo_transport import OKXDemoTransport
from okx_instruments import instrument_spec
from okx_orders import ACTIVE
from okx_reconcile import number
from okx_risk import SYMBOLS, positive
from okx_strategy_bridge import timestamp

PENDING_ALGO_STATES=frozenset({'live','pause','partially_effective','partially_failed'})

class DemoSnapshotError(RuntimeError):
    def __init__(self,message,*,stage='unknown',kind='unavailable'):
        super().__init__(message);self.stage=stage;self.kind=kind


def digits(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,40}", value):
        raise ValueError("Invalid response identity")
    return value


def canonical(rows):
    import json
    return sorted(rows, key=lambda row:json.dumps(row, sort_keys=True, separators=(",", ":")))


class DemoSnapshotReader:
    def __init__(self, transport, *, account_scope, expected_uid, symbol, clock,recovery_only=False):
        if not isinstance(transport, OKXDemoTransport) or transport.environment != "demo":
            raise ValueError("Fixed demo transport required")
        if not isinstance(account_scope, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", account_scope) or symbol not in SYMBOLS:
            raise ValueError("Configured account scope and symbol required")
        self.transport, self.scope, self.uid, self.symbol, self.clock = transport, account_scope, digits(expected_uid), symbol, clock
        self.recovery_only=recovery_only is True
        self.last_snapshot = None

    async def _read(self, kind, **params):
        response = await self.transport.account_read(kind, **params)
        if response.get("code") != "0" or not isinstance(response.get("data"), list):
            raise ValueError("Unsuccessful read")
        return response["data"]

    def _config(self, rows):
        if len(rows) != 1:
            raise ValueError("Account configuration missing")
        row = rows[0]
        if row.get("uid") != self.uid or row.get("posMode") != "net_mode" or row.get("acctLv") not in {"2", "3"}:
            raise ValueError("Wrong account or unsupported account mode")
        if not isinstance(row.get("perm"), str):
            raise ValueError("Unknown API permissions")
        permissions = row["perm"].split(",")
        if ("read_only" not in permissions or len(permissions) != len(set(permissions))
                or not set(permissions) <= {"read_only", "trade"}):
            raise ValueError("Unsupported API permissions")
        return {"position_mode":"net_mode", "account_level":row["acctLv"],
                "order_permission_verified":"trade" in permissions}

    async def _scan(self, kind, *, algo_type=None):
        found, ids, cursor = [], set(), None
        fill_keys = set()
        field = "ordId" if kind == "orders" else "algoId" if kind == "algos" else "billId"
        for _ in range(6):
            params = {"after":cursor}
            if algo_type is not None:
                params["algo_type"] = algo_type
            page = await self._read(kind, **params)
            if len(page) > 100:
                raise ValueError("Oversized inventory page")
            for row in page:
                ident = digits(row.get(field))
                if ident in ids or (cursor is not None and int(ident) >= int(cursor)):
                    raise ValueError("Duplicate or regressing inventory cursor")
                ids.add(ident)
                if kind in {"orders", "algos"} and (not isinstance(row.get("instId"), str) or not row["instId"]):
                    raise ValueError("Inventory instrument missing")
                if kind == "orders":
                    size, filled = positive(row.get("sz"), "size"), number(row.get("accFillSz"))
                    if (row.get("state") not in ACTIVE or filled >= size
                            or (row["state"] == "live" and filled != 0)
                            or (row["state"] == "partially_filled" and filled <= 0)):
                        raise ValueError("Inconsistent pending entry")
                elif kind == "algos":
                    if row.get("ordType") != algo_type or row.get("state") not in PENDING_ALGO_STATES:
                        raise ValueError("Inconsistent pending algo")
                else:
                    key = (row.get("instId"), digits(row.get("tradeId")))
                    if key in fill_keys or key[0] not in SYMBOLS or row.get("side") not in {"buy", "sell"}:
                        raise ValueError("Unexpected fill identity")
                    fill_keys.add(key)
                    digits(row.get("ordId")); digits(row.get("fillTime"))
                    if int(row["fillTime"]) > timestamp(self.clock()):
                        raise ValueError("Fill time is in the future")
                    positive(row.get("fillSz"), "fill size"); positive(row.get("fillPx"), "fill price")
                    number(row.get("fee"), allow_negative=True)
                    if row.get("feeCcy") != "USDT":
                        raise ValueError("Unsupported fill fee currency")
                    if row.get("fillPnl") not in (None, ""):
                        number(row["fillPnl"], allow_negative=True)
                found.append(row)
                if len(found) > 500:
                    raise ValueError("Inventory exceeds scan bound")
            if len(page) < 100:
                return canonical(found)
            cursor = page[-1][field]
        raise ValueError("Missing terminal inventory page")

    async def _algos(self):
        # Serial type scans also work reliably outside a pooled full snapshot.
        rows=[]
        for kind in ALGO_TYPES:
            rows.extend(await self._scan('algos',algo_type=kind))
        if len(rows) > 500 or len({row["algoId"] for row in rows}) != len(rows):
            raise ValueError("Ambiguous algo inventory")
        return canonical(rows)

    async def _pass(self, config=None):
        async with asyncio.TaskGroup() as group:
            tasks = {
                "balance":group.create_task(self._read("balance")),
                "leverage":group.create_task(self._read("leverage", symbol=self.symbol)),
                "instrument":group.create_task(self._read("instrument", symbol=self.symbol)),
                "positions":group.create_task(self._read("positions")),
                "orders":group.create_task(self._scan("orders")),
                "algos":group.create_task(self._algos()),
                "fills":group.create_task(self._scan("fills")),
            }
            if config is None:
                tasks["config"] = group.create_task(self._read("config"))
        data = {name:task.result() for name, task in tasks.items()}
        mode = config if config is not None else self._config(data.pop("config"))
        balance = data["balance"]
        if len(balance) != 1 or not isinstance(balance[0].get("details"), list) or len(balance[0]["details"]) != 1:
            raise ValueError("One USDT currency balance required")
        detail = balance[0]["details"][0]
        if detail.get("ccy") != "USDT":
            raise ValueError("Wrong balance currency")
        equity = positive(detail.get("eq"), "USDT equity")
        available = min(number(detail.get("availEq")), number(detail.get("availBal")))
        if available > equity:
            raise ValueError("Inconsistent available equity")
        lev = data["leverage"]
        if (len(lev) != 1 or lev[0].get("instId") != self.symbol or lev[0].get("mgnMode") != "isolated"
                or lev[0].get("posSide") != "net"):
            raise ValueError("Ambiguous isolated leverage")
        leverage = positive(lev[0].get("lever"), "leverage")
        if leverage != leverage.to_integral_value() or leverage > 3:
            raise ValueError("Unsupported leverage")
        if len(data["instrument"]) != 1:
            raise ValueError("Instrument metadata missing")
        instrument = data["instrument"][0]
        spec = instrument_spec(instrument)
        positive(instrument.get("maxMktSz"), "market maximum"); positive(instrument.get("maxStopSz"), "stop maximum")
        if spec["symbol"] != self.symbol or instrument.get("upcChg"):
            raise ValueError("Instrument differs or has pending parameter changes")
        positions, seen = data["positions"], set()
        for row in positions:
            number(row.get("pos"), allow_negative=True)
            key = tuple(row.get(k) for k in ("instId", "mgnMode", "posSide", "posId"))
            if any(not isinstance(k, str) or not k for k in key) or key in seen:
                raise ValueError("Ambiguous position inventory")
            seen.add(key)
        return dict(mode=mode, equity=str(equity), available=str(available),
            cash=None if detail.get("cashBal") in (None, "") else str(number(detail["cashBal"], allow_negative=True)), leverage=int(leverage),
            instrument=instrument, positions=canonical(positions), orders=data["orders"], algos=data["algos"], fills=data["fills"])

    async def collect(self):
        self.last_snapshot = None
        stage='snapshot_start'
        try:
            start = timestamp(self.clock())
            async with asyncio.timeout(5):
                async with self.transport.read_session():
                    # Verify the configured account before reading any account amounts.
                    stage='account_config'
                    config = self._config(await self._read("config"))
                    stage='inventory_first_pass'
                    first = await self._pass(config)
                    stage='inventory_second_pass'
                    second = await self._pass()
            stage='snapshot_consistency'
            end = timestamp(self.clock())
            if self.recovery_only and (first['cash'] is None or second['cash'] is None):
                raise ValueError('Recovery requires verified cash balance')
            stable=lambda data:{k:v for k,v in data.items() if not self.recovery_only or k not in {'equity','available'}}
            if stable(first) != stable(second) or not 0 <= end - start <= 5000:
                raise ValueError("Account changed or scan expired")
            snapshot = {"account_scope":self.scope, "symbol":self.symbol,
                "demo_verified":True, "complete":True, "monitor_healthy":True,
                # The oldest read time is used, not an artificially fresh finish time.
                "received_at_ms":start, "scan_finished_at_ms":end,
                "position_mode":second["mode"]["position_mode"],
                "order_permission_verified":second["mode"]["order_permission_verified"],
                "isolated_leverage":second["leverage"], "instrument":second["instrument"],
                "equity_usdt":second["equity"], "available_usdt":second["available"],
                "cash_balance_usdt":second["cash"],
                "positions":second["positions"], "pending_orders":second["orders"], "pending_algos":second["algos"],
                "recent_fills":second["fills"], "fill_history_complete":False, "fill_audit_ready":False}
            if self.recovery_only: snapshot['recovery_only']=True
            self.last_snapshot = deepcopy(snapshot)
            return snapshot
        except Exception as error:
            from okx_demo_read_errors import failure_kind
            raise DemoSnapshotError("Demo account snapshot unavailable or inconsistent",stage=stage,kind=failure_kind(error)) from None
