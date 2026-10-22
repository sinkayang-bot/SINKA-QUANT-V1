import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
test('migration defaults to a local nontrading plan without Cloud SDK',()=>{
  const result=spawnSync('bash',['firebase/migrate-demo-vm.sh'],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  const plan=JSON.parse(result.stdout);
  assert.equal(plan.executed,false);assert.equal(plan.starts_new_trade,false);
  assert.equal(plan.live_trading_enabled,false);assert.equal(plan.disk_auto_delete,false);
  assert.equal(plan.state,'original_sqlite_backup_and_binding');
});
test('unknown migration modes and extra parameters cannot deploy',()=>{
  for(const args of [['--approved'],['--approved-cost','--reset'],['--plan','--run']]){
    const result=spawnSync('bash',['firebase/migrate-demo-vm.sh',...args],{encoding:'utf8'});
    assert.notEqual(result.status,0);
  }
});
