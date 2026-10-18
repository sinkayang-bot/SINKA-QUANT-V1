"""Worker exit must release only its own lease, even on runtime failure."""
import sqlite3
from unittest.mock import patch

import pytest
import okx_demo_trading_worker as worker


@pytest.mark.parametrize('fails',[False,True])
@pytest.mark.parametrize('daily_limits',['0','1'])
def test_worker_releases_owned_lease_on_success_and_failure(tmp_path,fails,daily_limits):
    path=tmp_path/'ledger.sqlite'
    env=dict(RUN_DEMO_TRADING='1',DEMO_TRADING_AUTHORIZED='1',DEMO_LEDGER_PATH=str(path),
        DEMO_ACCOUNT_SCOPE='synthetic-scope',OKX_EXPECTED_UID='123',
        OKX_API_KEY='synthetic',OKX_API_SECRET='synthetic',OKX_API_PASSPHRASE='synthetic',DEMO_DAILY_LIMITS=daily_limits)
    async def stopped(supervisor,env):
        assert supervisor.session.authority.daily_limits_enabled==(daily_limits=='1')
        assert supervisor.session.authority.acquire()
        supervisor.session.authority.set_kill_switch(False)
        if fails: raise ValueError('synthetic runtime failure')
        return worker.outcome('verified_but_not_armed')
    with patch.object(worker,'run_supervisor',stopped):
        result,code=worker.execute(env)
    assert code==(2 if fails else 0)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT kill,expires FROM demo_authority').fetchone()==(1,0)
        assert db.execute('SELECT COUNT(*) FROM demo_intent').fetchone()[0]==0
