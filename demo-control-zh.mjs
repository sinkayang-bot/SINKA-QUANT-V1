const phases={
  tracked_exit_evidence_matches:'平倉成交與帳單核對通過，等待結算歸檔',
  tracked_exit_review_required:'平倉帳單尚未通過完整核對，禁止新進場',
  manual_close_evidence_matches:'平倉帳單核對通過，出場來源尚未確認',
  manual_close_review_required:'平倉帳單仍需核對，出場來源尚未確認',
  tracked_strategy_exit_closed_and_archived:'策略平倉已核對、結算並歸檔',
  tracked_test_exit_closed_and_archived:'測試平倉已核對、結算並歸檔',
  manual_test_closed_and_archived:'手動平倉測試已結算並歸檔',
  ready:'已就緒，尚未啟動交易', starting:'正在啟動', waiting_for_signal:'正在掃描，等待進場訊號',
  entry_gate_blocked:'出現候選訊號，但未通過進場條件', market_preflight_blocked:'候選幣種未通過帳戶檢查',
  native_protection_verified_monitor_required:'已核對原生止損止盈，持續監控交易',
  cycle_archived:'本筆交易已結算歸檔', idle_verified:'閒置帳本已核對', baseline_initialized:'初始帳本已建立',
  no_active_intent_no_submission:'恢復程序結束，沒有待處理交易意圖，也沒有送單',
  stopped_no_active_intent:'本輪已停止，沒有待處理交易意圖',
  verified_but_not_armed:'檢查完成，尚未允許進場', blocked:'檢查未通過，已阻擋執行',
  blocked_reconciliation_required:'核對未完成，已停止新進場', recovery_required:'需要先恢復核對',
  flat_requires_settlement:'持倉已平，等待成交帳單結算',
  flat_requires_emergency_settlement_review:'已核對平倉，仍需檢查帳單並結算',
  exit_acknowledged_query_required:'平倉指令已收到，仍須查詢成交結果',
  exit_unknown_no_retry:'平倉結果尚未確認，禁止重複送單',
  kill_switch_active:'停止開關已啟用，禁止新進場', lease_busy:'帳本正由另一執行器使用',
  shutdown_recovery_unresolved:'停止後的倉位核對尚未完成', disabled:'尚未啟用',
  unsubmitted_intent_preserved:'未送出的交易意圖已保留，需核對',
  stopped_unsubmitted_intent_retained:'已停止，未送出的交易意圖仍保留',
  existing_strategy_intent_preserved:'已有策略交易意圖，已保留並阻擋測試進場',
  emergency_exit_review_required:'需先核對先前緊急平倉',
};
const reasons={
  runtime_limit:'已達本輪執行時間上限', stop_requested:'收到停止指令',
  execution_test_cycle:'最低量進出場測試結束', runtime_failed:'執行發生異常',
  reconciliation_unavailable:'帳本或帳戶資料尚未完成一致性核對',
  demo_full_snapshot_inconsistent_or_expired:'帳戶快照不一致或已過期',
  signal_stop_distance:'停損距離不符合進場條件', signal_expired:'訊號或報價已過期',
  minimum_size_not_funded:'可用資金不足以符合最小下單量', price_drift:'價格偏離訊號過大',
  archived_idle_window:'歸檔帳本的恢復時窗不符合規則', idle_baseline:'閒置帳本基準或成交錨點未通過核對',
  idle_bills:'閒置期間帳單未通過核對', idle_cash:'帳戶現金與帳本不一致',
  idle_inventory:'帳戶仍有持倉或未完成委託', idle_fills:'成交紀錄與原帳本不一致',
  idle_archive_chain:'歷史歸檔完整性未通過核對',
};
export const phaseChinese=value=>phases[value]??'狀態尚未辨識，請查看診斷紀錄';
const reasonChinese=value=>reasons[value]??phases[value]??'檢查未通過，詳情保留於診斷紀錄';
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const record=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};

export function controlSummary(s){
  const result=record(s.last_result), worker=record(result.worker), closure=record(result.closure), review=record(result.review);
  const report=Object.keys(worker).length?worker:Object.keys(closure).length?closure:Object.keys(review).length?review:result;
  const historical=!s.runner_online || (!s.busy && s.phase==='ready');
  const lines=[historical?'最近一次執行結果（歷史紀錄）':'本次執行結果',
    '進度：'+(Object.keys(result).length?phaseChinese(report.phase??result.phase):'尚無執行紀錄')];
  if(report.acceptance_window===true&&integer(report.max_seconds))lines.push('延長驗收時限：'+Math.ceil(report.max_seconds/60)+' 分鐘；到期仍會停止，不保證出現訊號或自然出場');
  const ticks=report.ticks_observed??report.ticks;
  if(integer(ticks))lines.push('已完成掃描／處理：'+ticks+' 輪');
  const scan=record(report.scan??result.scan);
  if(integer(scan.universe)&&integer(scan.eligible))lines.push(`候選幣種：${scan.universe} 種；符合篩選：${scan.eligible} 種`);
  if(integer(scan.checked))lines.push('最新一輪已檢查：'+scan.checked+' 種');
  if(integer(scan.signals))lines.push('最新一輪找到訊號：'+scan.signals+' 個');
  if(integer(scan.breakout_not_met))lines.push('未達突破條件：'+scan.breakout_not_met+' 種');
  if(integer(scan.fetch_failed))lines.push('最新一輪資料讀取失敗：'+scan.fetch_failed+' 種');
  const counts=record(report.tick_counts);
  if(integer(counts.waiting_for_signal))lines.push('等待訊號：'+counts.waiting_for_signal+' 輪');
  if(report.selected_symbol??result.selected_symbol)lines.push('本次選定合約：'+(report.selected_symbol??result.selected_symbol));
  for(const key of ['stop_reason','gate_reason','audit_stage','reason'])if(report[key])lines.push(
    ({stop_reason:'停止原因',gate_reason:'進場受阻原因',audit_stage:'未通過的檢查',reason:'說明'})[key]+'：'+reasonChinese(report[key]));
  const checks=[['evidence_matches','成交帳單核對'],['automatic_exit_verified','自動平倉核對'],['protection_verified','止損止盈核對'],['native_protection_verified','原生保護單核對'],
    ['flat_verified','平倉核對'],['ledger_archived','交易歸檔'],['test_roundtrip_verified','測試進出場核對']];
  for(const [key,label] of checks)if(key in report)lines.push(label+'：'+(report[key]===true?'已確認':'尚未確認'));
  if(['manual_close_evidence_matches','manual_close_review_required'].includes(report.phase))lines.push('此核對未驗證原生停損／停利出場來源，不能據此判定為手動平倉。');
  const snapshotChecks=record(record(record(report.stop_diagnostics).protection_diagnostics).snapshot_checks??record(report.protection_diagnostics).snapshot_checks);
  for(const [key,label] of [['positions_stable','持倉'],['pending_orders_stable','掛單'],['pending_algos_stable','保護單'],['recent_fills_stable','近期成交'],['cash_balance_stable','現金餘額'],['instrument_stable','合約規格'],['other_snapshot_fields_stable','其他帳戶欄位']])if(snapshotChecks[key]===false)lines.push('兩次快照不一致：'+label);
  if(integer(report.baseline_history_recovered_count))lines.push('已補查舊成交：'+report.baseline_history_recovered_count+' 筆');
  for(const [key,label] of [['net_usdt','本筆淨損益'],['fees_usdt','本筆費用']])if(typeof report[key]==='string')lines.push(label+'：'+report[key]+' USDT');
  lines.push('持倉狀態以帳戶核對結果為準；「沒有交易意圖」不等於已確認帳戶空倉。');
  return lines.join('\n');
}

export function controlErrorChinese(code){
  return ({login_and_service_required:'請先登入 Google 並連線控制服務。',runner_offline:'執行器已離線，無法執行指令。',
    runner_busy:'執行器正在處理另一個指令。',command_pending:'已有指令等待處理，請勿重複按下。',
    command_in_progress:'指令正在送出，請稍候。',nothing_running:'目前沒有執行中的測試。',
    invalid_control_url:'控制服務網址不正確。',invalid_demo_response:'服務回覆未通過模擬模式檢查。',
    unauthorized:'登入已失效，請重新登入。',forbidden:'此帳戶沒有操作權限。',
    service_unavailable:'控制服務暫時無法使用。'})[code]??'連線或操作未完成，請更新狀態；不要重複送出交易指令。';
}
