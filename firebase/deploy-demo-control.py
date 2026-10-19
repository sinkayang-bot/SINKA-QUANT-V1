"""Explicit deployment of an owner-only demo command API, without exchange keys.
Creates a separate deny-all-client Firestore database; does not move the ledger.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error

PROJECT='sinka-quant'
REGION='asia-east1'
SERVICE='sinka-okx-demo-control'
DATABASE='sinka-demo-control'


def command(*args):
    result=subprocess.run(['gcloud',*args,'--project',PROJECT],capture_output=True,text=True)
    if result.returncode:raise RuntimeError('gcloud operation failed; inspect Cloud Console operation logs')
    return result.stdout.strip()


def rules():
    # Existing Google login -> Google Rules API only. Token stays in process memory.
    token=command('auth','print-access-token')
    base=f'https://firebaserules.googleapis.com/v1/projects/{PROJECT}'
    def call(method,url,body=None):
        request=urllib.request.Request(url,method=method,headers={
            'Authorization':'Bearer '+token,'Content-Type':'application/json'},
            data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
    source="rules_version = '2'; service cloud.firestore { match /databases/{database}/documents { match /{document=**} { allow read, write: if false; } } }"
    created=call('POST',base+'/rulesets',{'source':{'files':[{'name':'firestore.rules','content':source}]}})
    name=f'projects/{PROJECT}/releases/cloud.firestore/{DATABASE}'
    release={'name':name,'rulesetName':created['name']}
    try:call('GET','https://firebaserules.googleapis.com/v1/'+name)
    except urllib.error.HTTPError as error:
        if error.code!=404:raise
        call('POST',base+'/releases',release)
    else:call('PATCH','https://firebaserules.googleapis.com/v1/'+name+'?updateMask=rulesetName',release)
    actual=call('GET','https://firebaserules.googleapis.com/v1/'+name)
    if actual['rulesetName']!=created['name']:raise RuntimeError('Rules release not verified')


def deploy():
    print('stage: preflight',flush=True)
    existing=json.loads(command('run','services','describe','sinka-okx-readonly-status','--region',REGION,'--format=json'))
    env=existing['spec']['template']['spec']['containers'][0]['env']
    owner=next((row.get('value','') for row in env if row.get('name')=='SINKA_OWNER_UID'),'')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',owner):raise RuntimeError('Existing owner binding required')
    command('services','enable','firestore.googleapis.com','firebaserules.googleapis.com',
        'run.googleapis.com','artifactregistry.googleapis.com','cloudbuild.googleapis.com','identitytoolkit.googleapis.com')
    try:command('firestore','databases','describe','--database',DATABASE,'--format=value(name)')
    except RuntimeError:
        command('firestore','databases','create','--database',DATABASE,'--location',REGION,'--type=firestore-native','--delete-protection')
    print('stage: lock_named_database',flush=True);rules()
    sa=f'{SERVICE}@{PROJECT}.iam.gserviceaccount.com'
    try:command('iam','service-accounts','describe',sa)
    except RuntimeError:command('iam','service-accounts','create',SERVICE,'--display-name=SINKA demo command API (no exchange secrets)')
    command('projects','add-iam-policy-binding',PROJECT,'--member=serviceAccount:'+sa,
        '--role=roles/datastore.user',
        '--condition=expression=resource.name=="projects/'+PROJECT+'/databases/'+DATABASE+'",title=sinka_demo_control_only', '--quiet')
    role=command('iam','roles','describe','sinkaFirebaseRevocationReader','--format=value(name)')
    command('projects','add-iam-policy-binding',PROJECT,'--member=serviceAccount:'+sa,'--role='+role,'--condition=None','--quiet')
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='sinka-control-build-') as directory:
        target=Path(directory)
        shutil.copyfile(root/'backend/Dockerfile.okx-demo-control',target/'Dockerfile')
        for file in ('firebase_auth.py','okx_demo_control_store.py','okx_demo_control_app.py'):
            shutil.copyfile(root/'backend'/file,target/file)
        image=f'{REGION}-docker.pkg.dev/{PROJECT}/sinka-okx-readonly/demo-control:owner-only'
        print('stage: build_control_api',flush=True)
        digest=command('builds','submit',directory,'--region',REGION,'--tag',image,'--suppress-logs','--format=value(results.images[0].digest)')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):raise RuntimeError('Immutable build digest required')
        print('stage: deploy_control_api',flush=True)
        command('run','deploy',SERVICE,'--region',REGION,'--image',image.split(':')[0]+'@'+digest,
            '--service-account',sa,'--set-env-vars=GOOGLE_CLOUD_PROJECT='+PROJECT+',SINKA_OWNER_UID='+owner+',SINKA_DEMO_CONTROL=true',
            '--clear-secrets','--min-instances=0','--max-instances=1','--concurrency=4','--cpu=1','--memory=512Mi',
            '--timeout=30s','--cpu-throttling','--no-cpu-boost','--allow-unauthenticated','--quiet')
    number=command('projects','describe',PROJECT,'--format=value(projectNumber)')
    if number!='361301529939':raise RuntimeError('Unexpected project binding')
    url=f'https://{SERVICE}-{number}.{REGION}.run.app'
    with urllib.request.urlopen(url+'/health',timeout=60) as response:
        health=json.load(response)
    if health.get('mode')!='okx_demo_control' or health.get('live_trading_enabled') is not False:
        raise RuntimeError('Control health probe failed')
    for path in ('/api/demo/status','/api/demo/commands'):
        request=urllib.request.Request(url+path,method='POST' if path.endswith('commands') else 'GET',
            headers={'Content-Type':'application/json'},data=b'{"action":"start","request_id":"00000000-0000-0000-0000-000000000000"}' if path.endswith('commands') else None)
        try:
            urllib.request.urlopen(request,timeout=60)
            raise RuntimeError('Unauthenticated control route must be denied')
        except urllib.error.HTTPError as error:
            if error.code!=401:raise RuntimeError('Authentication denial probe failed') from None
    print(json.dumps({'phase':'control_deployed','service_url':url,'missing_token_denied':True,
        'live_trading_enabled':False,'trading_started':False,'runner_connected':False}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved',action='store_true')
    args=parser.parse_args()
    if not args.approved:parser.exit(2,'Deployment requires explicit --approved; creates billed Cloud resources.\n')
    try:deploy()
    except Exception:parser.exit(2,'Deployment stopped. No trading was started. Inspect the last stage and Cloud Console logs.\n')
