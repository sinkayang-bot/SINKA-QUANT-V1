import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
const SCRIPT=join(process.cwd(),'firebase/verify-paper-vm-restart.sh');
function runTest(fn){const dir=mkdtempSync(join(tmpdir(),'sinka-restart-'));try{const bin=join(dir,'bin');mkdirSync(bin);const log=join(dir,'calls');
writeFileSync(join(bin,'gcloud'),`#!/usr/bin/env python3
import os,sys,json,datetime
args=sys.argv[1:]
with open(os.environ['SINKA_RESTART_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
source='https://www.googleapis.com/compute/v1/projects/sinka-quant/zones/asia-east1-b/disks/sinka-paper-trial'
statefile=os.environ['SINKA_RESTART_LOG']+'.starts'
try: starts=int(open(statefile).read())
except FileNotFoundError: starts=0
if args[:3]==['compute','instances','start']:
 starts+=1
 with open(statefile,'w') as f:f.write(str(starts))
if args[:3]==['compute','instances','describe']:
 print(json.dumps({'id':'42','name':'sinka-paper-trial','status':('TERMINATED' if os.getenv('SINKA_RESTART_STOPPED') and starts==0 else 'RUNNING'),'machineType':'zones/asia-east1-b/machineTypes/e2-micro','scheduling':{'maxRunDuration':{'seconds':'1800'},'instanceTerminationAction':'STOP','automaticRestart':False},'disks':[{'boot':True,'autoDelete':False,'source':source}],'serviceAccounts':[],'networkInterfaces':[{'network':'projects/sinka-quant/global/networks/sinka-paper-trial-net'}],'labels':{'sinka-purpose':'paper-trial'}}))
elif args[:3]==['compute','disks','describe']:
 print(json.dumps({'id':'88','name':'sinka-paper-trial','selfLink':source,'sizeGb':'10','type':'zones/asia-east1-b/diskTypes/pd-standard','creationTimestamp':'2026-10-17T00:00:00Z'}))
elif args[:3]==['compute','instances','get-serial-port-output']:
 if os.getenv('SINKA_RESTART_STOPPED') and starts==0:
  print('not ready',file=sys.stderr)
  sys.exit(1)
 if not os.getenv('SINKA_RESTART_NO_LOG'):
  now=datetime.datetime.now(datetime.timezone.utc).isoformat()
  for status in ['starting','waiting_signal']:
   print(json.dumps({'mode':'local_paper','strategy':'SNR-5M-1','status':status,'active':True,'positionOpen':False,'closedTrades':0,'revision':(1 if os.getenv('SINKA_RESTART_FRESH') else 2),'quoteFresh':True,'at':now},separators=(',',':')))
`,{mode:0o755});
const env={...process.env,PATH:bin+':'+process.env.PATH,SINKA_RESTART_LOG:log};
fn({env,calls:()=>{try{return readFileSync(log,'utf8').trim().split('\n').map(JSON.parse);}catch{return [];}}});
}finally{rmSync(dir,{recursive:true,force:true});}}
const exec=(args,env)=>spawnSync('bash',[SCRIPT,...args],{encoding:'utf8',env});
test('restart default is a no-op plan',()=>runTest(h=>{const r=exec([],h.env);assert.equal(r.status,0,r.stderr);assert.equal(JSON.parse(r.stdout).executed,false);assert.deepEqual(h.calls(),[]);}));
test('restart helper reuses only existing bounded instance without creating or deleting',()=>runTest(h=>{
 const r=exec(['--run-authorized-trial'],h.env);assert.equal(r.status,0,r.stderr);const calls=h.calls();
 assert.equal(calls.filter(c=>c[2]==='stop').length,1);assert.equal(calls.filter(c=>c[2]==='start').length,1);
 assert.ok(calls.every(c=>!c.includes('create')&&!c.includes('delete')&&!c.includes('set-scheduling')&&!c.includes('set-metadata')));
 assert.match(r.stdout,/"restart_acceptance_complete": true/);
}));
test('no valid existing checkpoint prevents a restart',()=>runTest(h=>{
 const r=exec(['--run-authorized-trial'],{...h.env,SINKA_RESTART_NO_LOG:'1'});assert.notEqual(r.status,0);assert.ok(h.calls().every(c=>!['stop','start'].includes(c[2])));
}));
test('already stopped VM with unavailable serial warms up then performs full existing-VM restart',()=>runTest(h=>{
 const r=exec(['--run-authorized-trial'],{...h.env,SINKA_RESTART_STOPPED:'1'});assert.equal(r.status,0,r.stderr);
 const calls=h.calls();assert.equal(calls.filter(c=>c[2]==='start').length,2);assert.equal(calls.filter(c=>c[2]==='stop').length,1);
 assert.ok(calls.every(c=>!c.includes('create')&&!c.includes('delete')&&!c.includes('set-metadata')));
 assert.match(r.stdout,/"restart_acceptance_complete": true/);
}));
test('warmup refuses fresh-reset checkpoint and stops without doing a second restart',()=>runTest(h=>{
 const r=exec(['--run-authorized-trial'],{...h.env,SINKA_RESTART_STOPPED:'1',SINKA_RESTART_FRESH:'1'});assert.notEqual(r.status,0);
 const calls=h.calls();assert.equal(calls.filter(c=>c[2]==='start').length,1);assert.equal(calls.filter(c=>c[2]==='stop').length,1);
 assert.ok(!r.stdout.includes('"restart_acceptance_complete": true'));
}));
