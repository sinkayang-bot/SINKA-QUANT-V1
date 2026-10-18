"""Fixed diagnostic labels only. Never expose exception text or response data."""
import sqlite3

KINDS=frozenset({'timeout','transport_failure','validation_failed','ledger_read_error','multiple_read_failures','unavailable'})


def failure_kind(error):
    kind=getattr(error,'kind',None)
    if isinstance(kind,str) and kind in KINDS: return kind
    if isinstance(error,TimeoutError): return 'timeout'
    if isinstance(error,sqlite3.Error): return 'ledger_read_error'
    children=getattr(error,'exceptions',None)
    if isinstance(children,tuple):
        kinds={failure_kind(child) for child in children}
        return next(iter(kinds)) if len(kinds)==1 else 'multiple_read_failures'
    if type(error).__name__ in {'DemoTransportError','ReadTimeout','ConnectTimeout','ConnectError','ReadError','HTTPStatusError','RemoteProtocolError'}:
        return 'transport_failure'
    if isinstance(error,(ValueError,TypeError,KeyError,ArithmeticError)): return 'validation_failed'
    return 'unavailable'
