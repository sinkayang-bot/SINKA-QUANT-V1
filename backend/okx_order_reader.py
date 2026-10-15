"""Read-only OKX order lookup and reconciliation; no order submission."""
from okx_orders import parse_order, reconcile
from okx_readonly import OKXReadOnly

class OKXOrderReader(OKXReadOnly):
    async def order(self, instrument, order_id=None, client_order_id=None):
        if instrument not in self._allowed_symbols():
            raise ValueError("Instrument not allowlisted")
        if bool(order_id) == bool(client_order_id):
            raise ValueError("Supply exactly one order identifier")
        params = {"instId": instrument}
        if order_id:
            params["ordId"] = str(order_id)
        else:
            params["clOrdId"] = str(client_order_id)
        records = await self.get("/api/v5/trade/order", params)
        if len(records) != 1:
            raise ValueError("Expected exactly one OKX order")
        record = parse_order(records[0])
        if record.instrument != instrument:
            raise ValueError("Unexpected order instrument")
        if order_id and record.order_id != str(order_id):
            raise ValueError("Unexpected order ID")
        if client_order_id and record.client_order_id != str(client_order_id):
            raise ValueError("Unexpected client order ID")
        return record

    @staticmethod
    def _allowed_symbols():
        from okx_readonly import ALLOWED_SYMBOLS
        return ALLOWED_SYMBOLS

    async def refresh(self, previous):
        current = await self.order(previous.instrument, order_id=previous.order_id)
        return reconcile(previous, {
            "ordId": current.order_id, "clOrdId": current.client_order_id,
            "instId": current.instrument, "state": current.state,
            "sz": str(current.requested), "accFillSz": str(current.filled),
        })
