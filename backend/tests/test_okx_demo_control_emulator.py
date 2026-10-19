"""Real Firestore transaction races, run by the existing emulator CI job."""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest
import uuid
from okx_demo_control_store import FirestoreControlStore,ControlBlocked

@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'),'Firestore emulator required')
class ControlEmulatorTests(unittest.TestCase):
    def setUp(self):
        from google.cloud import firestore
        self.client=firestore.Client(project=os.getenv('GOOGLE_CLOUD_PROJECT','sinka-emulator'))
        self.store=FirestoreControlStore(self.client,clock=lambda:100000)
        self.store.ref=self.client.collection('control_tests').document(uuid.uuid4().hex)
        self.generation=self.store.acquire('runner')
    def tearDown(self):
        for row in self.store.ref.collection('requests').stream():row.reference.delete()
        self.store.ref.delete()
    def test_two_commands_race_only_one_is_accepted_and_claimed(self):
        def issue(_):
            try:self.store.issue('start',str(uuid.uuid4()));return True
            except ControlBlocked:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(issue,range(2)))
        self.assertEqual(sum(results),1)
        command=self.store.poll('runner',self.generation)
        self.assertEqual(command['action'],'start')
        self.assertIsNone(self.store.poll('runner',self.generation))
    def test_duplicate_request_survives_later_requests(self):
        request=str(uuid.uuid4());self.store.issue('start',request)
        self.store.poll('runner',self.generation)
        self.store.publish('runner',self.generation,request,{'phase':'stopped_no_active_intent'},finished=True)
        self.store.issue('audit',str(uuid.uuid4()))
        self.assertTrue(self.store.issue('start',request)['duplicate'])
