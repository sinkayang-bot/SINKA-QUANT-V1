import test from 'node:test';
import assert from 'node:assert/strict';
import {diagnosticSummary} from '../readonly-diagnostics.mjs';
const now = Date.parse('2026-10-16T19:00:00Z');
const context = {signedIn: true, serviceConfigured: true};
const valid = {phase:'verified',verified:true,checkedAt:new Date(now).toISOString(),validUntil:new Date(now+30000).toISOString()};
test('diagnostics exclude secrets, identifiers, account detail and raw errors', () => {
  const raw = {...valid, token:'SECRET_TOKEN', uid:'PRIVATE_UID', email:'PRIVATE_EMAIL', balance:'PRIVATE_BALANCE',error:'RAW_ERROR'};
  const output = diagnosticSummary(raw,{...context, user:raw},now);
  for (const secret of ['SECRET_TOKEN','PRIVATE_UID','PRIVATE_EMAIL','PRIVATE_BALANCE','RAW_ERROR']) assert.ok(!output.includes(secret));
  assert.equal(JSON.parse(output).verified_at_copy,true);
});
test('copied evidence expires even when a timer was delayed', () => {
  const result=JSON.parse(diagnosticSummary(valid,context,now+30000));
  assert.equal(result.query_state,'expired'); assert.equal(result.verified_at_copy,false);
  assert.equal(result.checked_at,null); assert.equal(result.valid_until,null);
});
test('unknown states and arbitrary timestamp strings cannot leak into diagnostics', () => {
  const output=diagnosticSummary({phase:'SECRET_VALUE',checkedAt:'SECRET_VALUE',validUntil:'SECRET_VALUE'},context,now);
  assert.ok(!output.includes('SECRET_VALUE')); assert.equal(JSON.parse(output).query_state,'unknown');
});
test('signed-out or unconfigured service cannot produce verified diagnostics', () => {
  for (const flags of [{signedIn:false,serviceConfigured:true},{signedIn:true,serviceConfigured:false}]) {
    const result=JSON.parse(diagnosticSummary(valid,flags,now));
    assert.equal(result.verified_at_copy,false); assert.equal(result.checked_at,null);
  }
});
