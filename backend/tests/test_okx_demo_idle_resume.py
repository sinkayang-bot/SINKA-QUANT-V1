"""GET-only gap reconciliation of a never-traded baseline; no exchange access."""
import asyncio
from test_okx_demo_reconciliation import InitializationTests


class IdleResumeTests(InitializationTests):
    def bootstrap_gap(self,elapsed=7200000):
        self.assertEqual(asyncio.run(self.runner.run_once(initialize_baseline=True))['phase'],'baseline_initialized')
        self.baseline=self.journal.db.execute('SELECT baseline_at FROM demo_fill_audit_meta').fetchone()[0]
        self.f.now+=elapsed

    def run_resume(self):
        return asyncio.run(self.runner.run_once(resume_idle=True))

    def test_explicit_resume_preserves_original_baseline_and_remains_killed(self):
        self.bootstrap_gap()
        self.assertEqual(asyncio.run(self.runner.run_once())['phase'],'blocked')
        self.assertEqual(self.run_resume()['phase'],'idle_verified')
        db=self.journal.db
        self.assertEqual(db.execute('SELECT baseline_at FROM demo_fill_audit_meta').fetchone()[0],self.baseline)
        self.assertEqual(db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_idle_resume').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_risk_entry').fetchone()[0],0)
        self.assertEqual(asyncio.run(self.runner.run_once())['phase'],'idle_verified')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_idle_resume').fetchone()[0],1)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_beyond_twelve_hours_never_refreshes_timestamp(self):
        self.bootstrap_gap(43200001)
        result=self.run_resume()
        self.assertEqual(result['phase'],'blocked')
        self.assertEqual(result['audit_stage'],'idle_resume')
        self.assertEqual(result['failure_kind'],'validation_failed')
        self.assertEqual(self.journal.db.execute('SELECT last_checked FROM demo_fill_audit_meta').fetchone()[0],self.baseline)

    def test_any_entry_reservation_blocks_even_with_empty_account(self):
        self.bootstrap_gap()
        self.journal.db.execute("INSERT INTO demo_risk_entry VALUES('used','2026-01-01',1,'old')")
        self.assertEqual(self.run_resume()['phase'],'blocked')
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_idle_resume').fetchone()[0],0)

    def test_emergency_tombstone_blocks_recovery(self):
        self.bootstrap_gap()
        self.journal.db.execute('CREATE TABLE demo_emergency_exit (entry_id TEXT)')
        self.journal.db.execute("INSERT INTO demo_emergency_exit VALUES('unresolved')")
        self.assertEqual(self.run_resume()['phase'],'blocked')

    def test_changed_cash_or_unknown_fill_never_refreshes(self):
        self.bootstrap_gap()
        for change in ('cash','fill'):
            with self.subTest(change=change):
                self.f.balance['details'][0]['cashBal']='9999' if change=='cash' else '10000'
                self.f.fills=[] if change=='cash' else [{'unknown':'fill'}]
                self.assertEqual(self.run_resume()['phase'],'blocked')
                self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_idle_resume').fetchone()[0],0)

    def test_unknown_bills_even_offsetting_net_zero_are_rejected(self):
        self.bootstrap_gap()
        row=dict(billId='901',ts=str(self.f.now-1),type='8',subType='173',ordId='',tradeId='',
            instId='BTC-USDT-SWAP',instType='SWAP',ccy='USDT',pnl='-1',fee='0',balChg='-1',sz='1',px='100')
        self.f.bills=[row|dict(billId='902',balChg='1',pnl='1'),row]
        result=self.run_resume()
        self.assertEqual(result['phase'],'blocked')
        self.assertEqual(result['audit_stage'],'idle_bills')
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_idle_resume').fetchone()[0],0)

    def test_record_write_failure_rolls_back_timestamp_and_receipt(self):
        self.bootstrap_gap()
        self.journal.db.execute("CREATE TRIGGER fail_resume BEFORE INSERT ON demo_idle_resume BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        result=self.run_resume()
        self.assertEqual(result['phase'],'blocked')
        self.assertEqual(result['audit_stage'],'idle_commit')
        self.assertEqual(result['failure_kind'],'ledger_read_error')
        self.assertEqual(self.journal.db.execute('SELECT last_checked FROM demo_fill_audit_meta').fetchone()[0],self.baseline)
        self.assertEqual(self.journal.db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],0)

    def test_lease_takeover_during_scan_cannot_commit_old_owner(self):
        self.bootstrap_gap()
        def takeover(f):
            if f.bill_calls==1:
                f.now+=30001
                self.other_owner=type(self.owner)(self.journal,account_scope='synthetic-v1',worker_id='second',clock=lambda:f.now)
                self.other_owner.acquire()
        self.f.bill_mutate=takeover
        self.assertEqual(self.run_resume()['phase'],'blocked')
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_idle_resume').fetchone()[0],0)
