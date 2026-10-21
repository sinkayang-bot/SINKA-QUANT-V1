import test from 'node:test';
import assert from 'node:assert/strict';
import {phaseChinese,controlSummary,controlErrorChinese} from '../demo-control-zh.mjs';
test('progress explains current scan counts in Chinese without raw phase codes',()=>{
  const text=controlSummary({runner_online:true,busy:true,last_result:{phase:'waiting_for_signal',ticks:54,
    scan:{universe:20,eligible:16,checked:16,signals:0,breakout_not_met:16,fetch_failed:0}}});
  assert.match(text,/等待進場訊號/);assert.match(text,/54 輪/);assert.match(text,/符合篩選：16/);
  assert.match(text,/未達突破條件：16/);assert.doesNotMatch(text,/waiting_for_signal/);
});
test('offline and newly reconnected runners label old results as historical',()=>{
  for(const state of [{runner_online:false},{runner_online:true,busy:false,phase:'ready'}]){
    const text=controlSummary({...state,last_result:{worker:{phase:'stopped_no_active_intent',ticks_observed:111,
      stop_reason:'runtime_limit',tick_counts:{waiting_for_signal:111}}}});
    assert.match(text,/歷史紀錄/);assert.match(text,/時間上限/);assert.match(text,/不等於已確認帳戶空倉/);
  }
});
test('failed checks and unknown statuses never claim successful verification',()=>{
  const text=controlSummary({runner_online:true,last_result:{worker:{phase:'blocked',audit_stage:'archived_idle_window',
    flat_verified:false,protection_verified:'true'}}});
  assert.match(text,/恢復時窗/);assert.match(text,/平倉核對：尚未確認/);
  assert.match(text,/止損止盈核對：尚未確認/);assert.match(phaseChinese('unexpected_phase'),/尚未辨識/);
  assert.doesNotMatch(controlErrorChinese('raw provider error / secret'),/raw provider|secret/);
});
test('verified closure and money are shown only when provided',()=>{
  const text=controlSummary({runner_online:true,last_result:{closure:{phase:'cycle_archived',ledger_archived:true,net_usdt:'-1.25',fees_usdt:'0.1'}}});
  assert.match(text,/交易歸檔：已確認/);assert.match(text,/本筆淨損益：-1.25 USDT/);
  assert.doesNotMatch(controlSummary({runner_online:true,last_result:{}}),/本筆淨損益/);
});
