"""Explicitly armed demo writes. No runtime imports this module yet."""
import re
from urllib.parse import urlencode

from okx_demo_transport import OKXDemoTransport, DEMO_HEADERS, DemoTransportError, amount
from okx_risk import SYMBOLS


class OKXDemoTradingTransport(OKXDemoTransport):
    def __init__(self, *args, enable_demo_orders=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.orders_enabled = enable_demo_orders is True

    def _require_orders(self):
        self._require_enabled()
        if not self.orders_enabled:
            raise DemoTransportError('Demo orders require an independent explicit opt-in')

    async def place_entry(self, body, headers, *, deadline_ms=None):
        self._require_orders()
        return await super().place_entry(body, headers, deadline_ms=deadline_ms)

    async def cancel_entry(self, body, headers):
        self._require_orders()
        return await super().cancel_entry(body, headers)

    async def reduce_position(self, body, headers):
        self._require_orders()
        if (headers != DEMO_HEADERS or not isinstance(body, dict)
                or set(body) != {'instId','tdMode','posSide','side','ordType','sz','clOrdId','reduceOnly'}
                or body['instId'] not in SYMBOLS or body['tdMode'] != 'isolated'
                or body['posSide'] != 'net' or body['side'] not in {'buy','sell'}
                or body['ordType'] != 'market' or body['reduceOnly'] is not True):
            raise ValueError('Only isolated net reduce-only market exits are supported')
        self.reduction_id(body['clOrdId'])
        amount(body['sz'])
        return await self._send('POST','/api/v5/trade/order',dict(body))

    @staticmethod
    def reduction_id(value):
        if not isinstance(value,str) or not re.fullmatch(r'sr[0-9a-f]{28}',value):
            raise ValueError('Tracked reduction client identity required')
        return value

    async def lookup_reduction(self,symbol,client_id):
        self._require_enabled()
        if symbol not in SYMBOLS:
            raise ValueError('Instrument not allowlisted')
        query=urlencode({'instId':symbol,'clOrdId':self.reduction_id(client_id)})
        return await self._send('GET','/api/v5/trade/order?'+query)
