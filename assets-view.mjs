const $ = id => document.getElementById(id);
const display = value => value === null ? '未提供' : String(value);
const sides = {net:'淨倉',long:'多倉',short:'空倉',buy:'買入',sell:'賣出',cross:'全倉',isolated:'逐倉',live:'未成交',partially_filled:'部分成交'};
function table(id, rows) {
  const body = $(id);
  body.replaceChildren();
  for (const values of rows) {
    const tr = document.createElement('tr');
    for (const value of values) {
      const td = document.createElement('td'); td.textContent = display(value); tr.append(td);
    }
    body.append(tr);
  }
}
export function renderAssets(state, messages) {
  if ($('asset-connection-state')) {
    $('asset-connection-state').textContent=state.verified?'本次已更新':state.httpStatus===404?'待後端更新':
      state.phase==='signed_out'?'尚未登入':state.phase==='checking'?'正在讀取':state.phase==='expired'?'已過期':
      state.phase==='signed_in'?'尚未查詢':'目前無法確認';
    $('asset-connection-note').textContent=state.verified?'模擬帳戶資料見下方資產面板，最多 30 秒有效。':
      '登入後可手動查詢；失敗、過期或登出時會清除資料。';
  }
  $('asset-status').textContent = state.httpStatus === 404 ? '資產面板正在準備後端更新，請稍後再試。' :
    state.phase === 'verified' ? '本次模擬帳戶資產查詢通過，交易功能關閉。' :
    state.phase === 'signed_in' ? '已登入，按「更新模擬帳戶資產」讀取本次資料。' :
    state.phase === 'expired' ? '資產資料已過期並清除，請手動更新。' : messages[state.phase] || messages.unavailable;
  $('asset-private').hidden = !state.verified;
  $('asset-checked').textContent = state.verified ? '查詢完成：' + new Date(state.checkedAt).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'}) + '（台灣）；最多 30 秒內有效。' : '';
  $('asset-equity').textContent = state.verified ? display(state.assets.summary.total_equity_usd) : '—';
  const data = state.verified ? state.assets : {currencies:[],positions:[],pending_orders:[]};
  table('asset-currencies',data.currencies.map(r=>[r.currency,r.equity,r.cash_balance,r.available_balance,r.unrealized_pnl]));
  table('asset-positions',data.positions.map(r=>[r.instrument,sides[r.side],sides[r.margin_mode],r.contracts,r.average_price,r.mark_price,r.unrealized_pnl,r.currency,r.notional_usd]));
  table('asset-orders',data.pending_orders.map(r=>[r.instrument,sides[r.side],sides[r.position_side],sides[r.state],r.contracts,r.filled_contracts,r.price]));
  for (const [id,items,label] of [['asset-currencies-empty',data.currencies,'本次未回傳幣別資料。'],
    ['asset-positions-empty',data.positions,'本次查詢沒有非零持倉。'],['asset-orders-empty',data.pending_orders,'本次查詢沒有未完成委託。']]) {
    $(id).textContent = state.verified && !items.length ? label : '';
  }
}
