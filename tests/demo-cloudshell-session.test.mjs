import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';

function fixture(){
  const home=mkdtempSync(join(tmpdir(),'sinka-session-'));
  const bin=join(home,'bin');mkdirSync(bin);
  for(const [name,script] of Object.entries({
    tmux:'if [ "$1" = has-session ]; then exit "${EXISTING_SESSION:-1}"; fi\nprintf "%s\\n" "$*" >> "$HOME/tmux-call"',
    pgrep:'exit "${EXISTING_PROCESS:-1}"',
  }))writeFileSync(join(bin,name),'#!/bin/sh\n'+script+'\n',{mode:0o755});
  mkdirSync(join(home,'sinka-demo-trial'));
  for(const name of ['account.json','ledger.sqlite'])writeFileSync(join(home,'sinka-demo-trial',name),'existing-state');
  mkdirSync(join(home,'sinka-demo-venv','bin'),{recursive:true});
  writeFileSync(join(home,'sinka-demo-venv','bin','python'),'#!/bin/sh\nexit 99\n',{mode:0o755});
  return {home,run:(args=[],env={})=>spawnSync('bash',['firebase/run-demo-cloudshell.sh',...args],{
    encoding:'utf8',env:{...process.env,HOME:home,PATH:bin+':'+process.env.PATH,...env}})};
}
test('status and existing processes never launch another controller',()=>{
  const f=fixture();try{
    assert.match(f.run().stdout,/沒有找到/);
    assert.match(f.run(['--connect'],{EXISTING_SESSION:'0'}).stdout,/工作階段已存在/);
    assert.match(f.run(['--connect'],{EXISTING_PROCESS:'0'}).stdout,/既有執行器/);
    assert.throws(()=>readFileSync(join(f.home,'tmux-call')));
  }finally{rmSync(f.home,{recursive:true,force:true});}
});
test('connect only starts the bounded command controller and preserves ledger',()=>{
  const f=fixture();try{
    const r=f.run(['--connect']);assert.equal(r.status,0,r.stderr);
    const command=readFileSync(join(f.home,'tmux-call'),'utf8');
    assert.match(command,/new-session -d -s sinka-demo-control/);
    assert.match(command,/okx_demo_control_runner.py --connect --seconds 14400/);
    assert.doesNotMatch(command,/--run|--test-entry|--initialize-baseline/);
    assert.equal(readFileSync(join(f.home,'sinka-demo-trial','ledger.sqlite'),'utf8'),'existing-state');
  }finally{rmSync(f.home,{recursive:true,force:true});}
});
test('retired migration source and unknown flags cannot launch',()=>{
  const f=fixture();try{
    writeFileSync(join(f.home,'sinka-demo-trial','migrated-to-vm.json'),'{}');
    assert.equal(f.run(['--connect']).status,2);
    assert.equal(f.run(['--run']).status,2);
    assert.throws(()=>readFileSync(join(f.home,'tmux-call')));
  }finally{rmSync(f.home,{recursive:true,force:true});}
});
