"""Deployment diagnostics never print token payloads or create on permission errors."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[2]/'firebase/deploy-demo-control.py'
spec=importlib.util.spec_from_file_location('demo_control_deploy_candidate',path)
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)

class DeploymentTests(unittest.TestCase):
    def test_fixed_diagnostics_do_not_return_cloud_error_payload(self):
        error=deploy.CloudCommandError(('auth','print-access-token'),1,'PERMISSION_DENIED synthetic-secret-token')
        self.assertEqual(error.kind,'PERMISSION_DENIED')
        self.assertNotIn('synthetic-secret-token',json.dumps(error.diagnostic))
        self.assertNotIn('synthetic-secret-token',str(error))
    def test_permission_error_does_not_attempt_database_creation(self):
        calls=[]
        def command(*args):
            calls.append(args)
            if args[:3]==('run','services','describe'):
                return json.dumps({'spec':{'template':{'spec':{'containers':[{'env':[{'name':'SINKA_OWNER_UID','value':'synthetic-owner'}]}]}}}})
            if args[:2]==('services','enable'):return ''
            raise deploy.CloudCommandError(args,1,'PERMISSION_DENIED')
        with patch.object(deploy,'command',side_effect=command),redirect_stdout(io.StringIO()):
            with self.assertRaises(deploy.CloudCommandError):deploy.deploy()
        self.assertFalse(any(args[:3]==('firestore','databases','create') for args in calls))
    def test_missing_owner_stops_before_any_mutation(self):
        calls=[]
        def command(*args):
            calls.append(args)
            return json.dumps({'spec':{'template':{'spec':{'containers':[{'env':[]}]}}}})
        with patch.object(deploy,'command',side_effect=command),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'owner'):deploy.deploy()
        self.assertEqual(len(calls),1)
    def test_only_not_found_can_trigger_database_creation(self):
        error=deploy.CloudCommandError(('firestore','databases','describe'),1,'The database does not exist')
        self.assertEqual(error.kind,'NOT_FOUND')
        self.assertEqual(deploy.CloudCommandError(('firestore','databases','describe'),1,'SERVICE_DISABLED').kind,'SERVICE_DISABLED')
