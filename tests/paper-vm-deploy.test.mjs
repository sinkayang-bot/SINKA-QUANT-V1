import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,rmSync,mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
const ROOT=process.cwd(),SCRIPT=join(ROOT,'firebase/deploy-paper-vm.sh');
function harness(run){const dir=mkdtempSync(join(tmpdir(),'sinka-vm-deploy-'));try{return run(dir);}finally{rmSync(dir,{recursive:true,force:true});}}
function mock(dir){
  const bin=join(dir,'bin');mkdirSync(bin);
  const log=join(dir,'calls.jsonl');
  writeFileSync(join(bin,'gcloud'),`#!/usr/bin/env python3
import sys,json,os
args=sys.argv[1:]
with open(os.environ['SINKA_TEST_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[:2]==['auth','list']: print('test-account')
elif args[:2]==['services','list']: print('compute.googleapis.com')
elif args[:3]==['compute','instances','describe']:
 print(json.dumps({'name':'sinka-paper-trial','status':'RUNNING','machineType':'zones/asia-east1-b/machineTypes/e2-micro','scheduling':{'maxRunDuration':{'seconds':'1800'},'instanceTerminationAction':'STOP','automaticRestart':False},'disks':[{'boot':True,'autoDelete':False}],'serviceAccounts':[],'networkInterfaces':[{'network':'projects/sinka-quant/global/networks/sinka-paper-trial-net'}],'labels':{'sinka-purpose':'paper-trial'}}))
elif args[:3]==['compute','instances','get-serial-port-output']: print('Booting, no worker heartbeat yet')
elif args[-1:] == ['--format=value(name)'] and os.getenv('SINKA_TEST_COLLISION'): print('sinka-paper-trial')
`,{mode:0o755});
  return {env:{...process.env,PATH:bin+':'+process.env.PATH,SINKA_TEST_LOG:log},calls:()=>{try{return readFileSync(log,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);}catch{return [];}}};
}
const exec=(args,env)=>spawnSync('bash',[SCRIPT,...args],{cwd:ROOT,encoding:'utf8',env});
test('default plan makes no cloud call or paid resource',()=>harness(dir=>{
  const h=mock(dir),r=exec([],h.env);assert.equal(r.status,0,r.stderr);assert.equal(JSON.parse(r.stdout).executed,false);assert.deepEqual(h.calls(),[]);
}));
test('unknown approval or unsupported runtime override cannot deploy',()=>harness(dir=>{
  const h=mock(dir);for(const args of [['--approved'],['--live'],['--approved-cost','--24h']])assert.notEqual(exec(args,h.env).status,0);assert.deepEqual(h.calls(),[]);
}));
test('rendered source is bounded, checksummed, isolated and only locally written',()=>harness(dir=>{
  const h=mock(dir),out=join(dir,'startup.sh'),r=exec(['--render-startup',out],h.env);assert.equal(r.status,0,r.stderr);
  const source=readFileSync(out,'utf8');assert.ok(source.length<200000);assert.match(source,/sha256sum --check --status/);assert.match(source,/--network none/);assert.match(source,/shutdown -h \+25/);assert.match(source,/--max-seconds 900/);assert.match(source,/--cap-drop=ALL/);assert.ok(!source.includes('gcloud secrets'));assert.deepEqual(h.calls(),[]);
  assert.equal(spawnSync('bash',['-n',out]).status,0);
}));
test('approved plan creates only named bounded VM with no identity or ingress grant',()=>harness(dir=>{
  const h=mock(dir),r=exec(['--approved-cost'],h.env);assert.equal(r.status,0,r.stderr);const calls=h.calls();
  const creates=calls.filter(c=>c.includes('create'));assert.equal(creates.length,4);
  const vm=creates.find(c=>c[1]==='instances');for(const flag of ['--max-run-duration=30m','--instance-termination-action=STOP','--no-service-account','--no-scopes','--no-restart-on-failure','--no-boot-disk-auto-delete'])assert.ok(vm.includes(flag));
  const firewall=creates.find(c=>c[1]==='firewall-rules');assert.ok(firewall.includes('--action=DENY'));
  assert.ok(calls.every(c=>!c.includes('add-iam-policy-binding')&&!c.includes('run')&&!c.includes('delete')));
  assert.match(r.stdout,/"cloud_acceptance_complete": false/);
}));
test('existing resource collision blocks creation instead of modifying it',()=>harness(dir=>{
  const h=mock(dir),r=exec(['--approved-cost'],{...h.env,SINKA_TEST_COLLISION:'1'});assert.notEqual(r.status,0);assert.ok(h.calls().every(c=>!c.includes('create')));
}));
test('verify is read-only and does not claim a VM boot is full acceptance',()=>harness(dir=>{
  const h=mock(dir),r=exec(['--verify'],h.env);assert.equal(r.status,0,r.stderr);assert.equal(h.calls().length,2);assert.ok(!JSON.parse(r.stdout).cloud_acceptance_complete);
}));
