"""No GCP or OKX traffic: transactional mailbox + owner-auth API + fixed executor."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch,MagicMock

from fastapi.testclient import TestClient
from okx_demo_control_app import create_app
from firebase_auth import verify_owner_token
from okx_demo_control_store import FirestoreControlStore,ControlBlocked,public_status
from okx_demo_control_runner import safe_report,arguments,effective_report,CommandProcess

class Snapshot:
    def __init__(self,data):self.data=deepcopy(data);self.exists=data is not None
    def to_dict(self):return deepcopy(self.data)
class Document:
    def __init__(self,store,key='state'):self.store,self.key=store,key
    def get(self,transaction=None):return Snapshot(self.store.docs.get(self.key))
    def collection(self,name):return self
    def document(self,key):return Document(self.store,key)
class Transaction:
    def __init__(self):self.pending=[]
    def set(self,ref,data):self.pending.append((ref.key,deepcopy(data)))
class MemoryStore(FirestoreControlStore):
    def __init__(self):
        self.docs={};self.now=100000;self.clock=lambda:self.now;self.ref=Document(self)
    def _transaction(self,operation):
        state=deepcopy(self.docs.get('state',{}));transaction=Transaction()
        result=operation(state,transaction)
        for key,data in transaction.pending:self.docs[key]=data
        self.docs['state']=state
        return result

class ControlTests(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore();self.runner='runner-a';self.generation=self.store.acquire(self.runner)
    def issue(self,action):
        request=str(uuid.uuid4());self.store.issue(action,request);return request
    def test_no_runner_no_commands(self):
        with self.assertRaisesRegex(ControlBlocked,'offline'):MemoryStore().issue('start',str(uuid.uuid4()))
    def test_exactly_once_and_persistent_request_id(self):
        request=self.issue('start')
        self.assertTrue(self.store.issue('start',request)['duplicate'])
        command=self.store.poll(self.runner,self.generation)
        self.assertEqual(command['request_id'],request)
        self.assertIsNone(self.store.poll(self.runner,self.generation))
        self.store.publish(self.runner,self.generation,request,{'phase':'stopped_no_active_intent'},finished=True)
        self.issue('audit');self.store.poll(self.runner,self.generation)
        self.assertTrue(self.store.issue('start',request)['duplicate'])
        with self.assertRaisesRegex(ControlBlocked,'conflict'):self.store.issue('recover',request)
    def test_expired_and_previous_generation_commands_never_run(self):
        self.issue('start');self.store.now+=31000
        self.generation=self.store.acquire(self.runner)
        self.assertIsNone(self.store.poll(self.runner,self.generation))
        self.assertFalse(self.store.status()['busy'])
    def test_stale_runner_cannot_publish_or_take_over(self):
        request=self.issue('start');self.store.poll(self.runner,self.generation)
        with self.assertRaises(ControlBlocked):self.store.acquire('runner-b')
        self.store.now+=16000;other=self.store.acquire('runner-b')
        with self.assertRaises(ControlBlocked):self.store.publish(self.runner,self.generation,request,{'phase':'ok'})
        self.assertTrue(self.store.status()['recovery_required'])
        self.issue('start');self.assertIsNone(self.store.poll('runner-b',other))
    def test_stop_targets_only_the_running_request(self):
        active=self.issue('start');self.store.poll(self.runner,self.generation)
        self.issue('stop');command=self.store.poll(self.runner,self.generation)
        self.assertEqual(command['target'],active)
        self.store.publish(self.runner,self.generation,active,{'phase':'stopped_no_active_intent'},finished=True)
        with self.assertRaisesRegex(ControlBlocked,'nothing_running'):self.issue('stop')
    def test_failed_results_require_recovery_but_archival_clears_it(self):
        request=self.issue('start');self.store.poll(self.runner,self.generation)
        self.store.publish(self.runner,self.generation,request,{'phase':'blocked_reconciliation_required'},finished=True)
        self.assertTrue(self.store.status()['recovery_required'])
        request=self.issue('settle');self.store.poll(self.runner,self.generation)
        self.store.publish(self.runner,self.generation,request,{'phase':'tracked_strategy_exit_closed_and_archived'},finished=True)
        self.assertFalse(self.store.status()['recovery_required'])
        self.assertEqual(len(self.store.status()['history']),2)
    def test_unknown_action_parameters_and_offline_status(self):
        for action in ('live','exec','start --seconds 9999'):
            with self.assertRaises(ControlBlocked):self.store.issue(action,str(uuid.uuid4()))
        self.store.now+=11000;self.assertFalse(self.store.status()['runner_online'])
    def test_sanitizer_excludes_raw_exchange_responses_and_keys(self):
        value=safe_report({'phase':'trial_finished','api_key':'synthetic-secret','data':[{'ordId':'123'}],
            'worker':{'phase':'blocked_reconciliation_required','raw_error':'synthetic-secret',
                'protection_diagnostics':{'consistency_checks':{'snapshot_stable':False}}}})
        self.assertNotIn('synthetic-secret',str(value));self.assertNotIn('ordId',str(value))
        result=effective_report(value)
        self.assertEqual(result['phase'],'blocked_reconciliation_required')
        self.assertIs(result['live_trading_enabled'],False)
    def test_executor_has_no_arbitrary_arguments(self):
        self.assertIn('--run',arguments('recover'));self.assertIn('--recover-only',arguments('recover'))
        self.assertEqual(arguments('settle'),['--close-reviewed-trade'])
        with self.assertRaises(KeyError):arguments('live')
        fake=MagicMock();fake.stdout=[]
        with patch('okx_demo_control_runner.subprocess.Popen',return_value=fake) as popen:
            process=CommandProcess('start',123)
            process.reader_thread.join(timeout=1)
            args,kwargs=popen.call_args
            self.assertNotIn('shell',kwargs)
            self.assertEqual(kwargs['pass_fds'],(123,))
            self.assertEqual(args[0][-1],'900')

    def test_idle_audit_diagnostic_survives_control_report_without_raw_error(self):
        value=safe_report({'worker':{'phase':'blocked','audit_stage':'archived_idle_window',
            'failure_kind':'validation_failed','raw_error':'secret account details'}})
        self.assertEqual(value['worker']['audit_stage'],'archived_idle_window')
        self.assertNotIn('secret',str(value))

    def test_entry_failure_stage_survives_final_report(self):
        value=safe_report({'worker':{'stop_diagnostics':{'phase':'blocked_reconciliation_required',
            'entry_stage':'entry_plan','plan_rejection':'source_freshness',
            'read_stage':'unknown','audit_stage':'unknown','raw_error':'private account'}}})
        detail=value['worker']['stop_diagnostics']
        self.assertEqual(detail['entry_stage'],'entry_plan')
        self.assertEqual(detail['plan_rejection'],'source_freshness')
        self.assertNotIn('private',str(value))

    def test_last_entry_block_keeps_stop_diagnostics_in_final_control_report(self):
        value=safe_report({'worker':{'phase':'stopped_no_active_intent','last_entry_block':{
            'gate_reason':'signal_stop_distance','ticks':10,'selected_symbol':'LTC-USDT-SWAP',
            'selected_strategy':'SNR-5M-FAST-1','entry_diagnostics':{
                'stop_rejection':'below_minimum','stop_distance_pct':'0.12000000',
                'min_stop_pct':'0.2','max_stop_pct':'3','api_key':'private'}}}})
        block=value['worker']['last_entry_block']
        self.assertEqual(block['selected_symbol'],'LTC-USDT-SWAP')
        self.assertEqual(block['entry_diagnostics']['stop_distance_pct'],'0.12000000')
        self.assertNotIn('private',str(value))
    def test_api_denies_missing_auth_and_wrong_owner_and_disabled(self):
        app=create_app(self.store);client=TestClient(app)
        with patch.dict(os.environ,{'SINKA_OWNER_UID':'synthetic-owner','SINKA_DEMO_CONTROL':'true'}):
            self.assertEqual(client.get('/api/demo/status').status_code,401)
            self.assertEqual(client.post('/api/demo/commands',json={'action':'start','request_id':str(uuid.uuid4())}).status_code,401)
            with patch('firebase_auth._firebase_app',return_value=object()),patch('firebase_auth.auth.verify_id_token',return_value={'uid':'other'}):
                self.assertEqual(client.get('/api/demo/status',headers={'Authorization':'Bearer synthetic'}).status_code,403)
        app.dependency_overrides[verify_owner_token]=lambda:'synthetic-owner'
        with patch.dict(os.environ,{'SINKA_DEMO_CONTROL':'false'}):
            self.assertEqual(client.get('/api/demo/status').status_code,503)
    def test_api_owner_roundtrip_and_strict_payload(self):
        app=create_app(self.store);app.dependency_overrides[verify_owner_token]=lambda:'synthetic-owner'
        client=TestClient(app)
        with patch.dict(os.environ,{'SINKA_DEMO_CONTROL':'true'}):
            request=str(uuid.uuid4())
            response=client.post('/api/demo/commands',json={'action':'start','request_id':request})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['accepted'])
            command=self.store.poll(self.runner,self.generation)
            self.store.publish(self.runner,self.generation,command['request_id'],{'phase':'waiting_for_signal'})
            status=client.get('/api/demo/status')
            self.assertTrue(status.json()['busy']);self.assertEqual(status.json()['phase'],'waiting_for_signal')
            self.assertIn('no-store',status.headers['Cache-Control'])
            self.assertEqual(client.post('/api/demo/commands',json={'action':'start','request_id':str(uuid.uuid4()),'api_key':'never-accepted'}).status_code,422)
            self.assertEqual(client.post('/api/demo/commands',json={'action':'live','request_id':str(uuid.uuid4())}).status_code,409)


class RunnerDisconnectTests(unittest.TestCase):
    def test_poll_failure_records_stage_without_replaying_commands(self):
        import okx_demo_control_runner as runner
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'sinka-demo-trial';root.mkdir()
            (root/'ledger.sqlite').touch();(root/'account.json').write_text('{}')
            store=MagicMock();store.acquire.return_value=1
            store.poll.side_effect=RuntimeError('private server data')
            with patch.object(runner.Path,'home',return_value=Path(folder)):
                with self.assertRaises(runner.RunnerConnectionError) as caught:
                    runner.run(store,seconds=60)
            self.assertEqual(caught.exception.stage,'poll')
            self.assertNotIn('private',str(caught.exception))
            self.assertEqual(store.poll.call_count,1)
            store.publish.assert_not_called()

    def test_idle_runtime_end_reports_normal_exit(self):
        import okx_demo_control_runner as runner
        import io
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'sinka-demo-trial';root.mkdir()
            (root/'ledger.sqlite').touch();(root/'account.json').write_text('{}')
            store=MagicMock();store.acquire.return_value=1
            output=io.StringIO()
            with patch.object(runner.Path,'home',return_value=Path(folder)),patch('sys.stdout',output):
                runner.run(store,seconds=0)
            self.assertIn('runtime_limit',output.getvalue())
            store.poll.assert_not_called()
