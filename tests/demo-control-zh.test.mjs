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

test('review success is explained without claiming archival or natural target exit',()=>{
  const text=controlSummary({runner_online:true,last_result:{phase:'tracked_exit_evidence_matches',review:{
    phase:'tracked_exit_evidence_matches',evidence_matches:true,automatic_exit_verified:true,
    baseline_history_recovered_count:42,fees_usdt:'-0.1'}}});
  assert.match(text,/等待結算歸檔/);assert.match(text,/成交帳單核對：已確認/);
  assert.match(text,/自動平倉核對：已確認/);assert.match(text,/已補查舊成交：42 筆/);
  assert.doesNotMatch(text,/尚未辨識|交易歸檔：已確認/);
  assert.match(phaseChinese('tracked_strategy_exit_closed_and_archived'),/結算並歸檔/);
  assert.match(phaseChinese('tracked_exit_review_required'),/尚未通過/);
});
test('long acceptance explains its actual worker duration without claiming a trade',()=>{
  const text=controlSummary({runner_online:true,busy:true,last_result:{worker:{phase:'waiting_for_signal',acceptance_window:true,max_seconds:7200}}});
  assert.match(text,/延長驗收時限：120 分鐘/);
  assert.match(text,/不保證出現訊號或自然出場/);
  assert.doesNotMatch(text,/測試進出場核對：已確認/);
});

test('unattributed close never asserts a human close and snapshot failures name only categories',()=>{
 const text=controlSummary({runner_online:true,last_result:{review:{phase:'manual_close_evidence_matches',automatic_exit_verified:false}}});
 assert.match(text,/出場來源尚未確認/);assert.match(text,/不能據此判定為手動平倉/);
 assert.doesNotMatch(text,/手動平倉帳單核對通過/);
 const failed=controlSummary({runner_online:true,last_result:{worker:{phase:'blocked_reconciliation_required',stop_diagnostics:{protection_diagnostics:{snapshot_checks:{positions_stable:false,cash_balance_stable:true}}}}}});
 assert.match(failed,/兩次快照不一致：持倉/);assert.doesNotMatch(failed,/兩次快照不一致：現金/);
});

test('native exit proof is distinct from emergency reduction and does not claim archival',()=>{
 const text=controlSummary({runner_online:true,last_result:{review:{phase:'native_exit_evidence_matches',native_exit_verified:true,automatic_exit_verified:false}}});
 assert.match(text,/原生停損／停利出場關聯：已確認/);
 assert.match(text,/尚未結算歸檔/);assert.doesNotMatch(text,/出場來源尚未確認|不能據此判定/);
});

test('native transition and reporting delay are pending rather than successful exit',()=>{
 assert.match(phaseChinese('native_exit_transition_pending'),/最多 15 秒/);
 assert.match(phaseChinese('native_settlement_pending'),/等待成交帳單/);
 assert.doesNotMatch(phaseChinese('native_exit_transition_pending'),/出場關聯核對通過/);
});
