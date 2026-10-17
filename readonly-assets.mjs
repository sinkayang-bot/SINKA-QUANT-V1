import {freshnessEvidence, createStatusSession} from './readonly-session.mjs?v=countdown-1';

const SYMBOLS = new Set(['BTC-USDT-SWAP','ETH-USDT-SWAP','SOL-USDT-SWAP','XRP-USDT-SWAP','DOGE-USDT-SWAP']);
const numeric = value => value === null || (typeof value === 'string' && value.length <= 96 && /^-?\d+(?:\.\d+)?$/.test(value));
const currency = value => typeof value === 'string' && /^[A-Z0-9]{1,24}$/.test(value);
const list = (value, max) => Array.isArray(value) && value.length <= max && value.every(row => row && typeof row === 'object' && !Array.isArray(row));
const pick = (row, names) => Object.fromEntries(names.map(name => [name, row[name]]));
function comparePositiveDecimals(a,b) {
  const [ai,af='']=a.replace(/^-/, '').split('.'), [bi,bf='']=b.replace(/^-/, '').split('.');
  const scale=Math.max(af.length,bf.length);
  const left=BigInt(ai+af.padEnd(scale,'0')), right=BigInt(bi+bf.padEnd(scale,'0'));
  return left<right?-1:left>right?1:0;
}

export function verifiedAssets(data, now) {
  const evidence = freshnessEvidence(data, now);
  if (!evidence || data.schema_version !== 1 || data.atomic !== false ||
      data.scope !== 'trading_account_and_five_monitored_swaps' ||
      !data.summary || !numeric(data.summary.total_equity_usd) ||
      !list(data.currencies,1000) || !list(data.positions,1000) || !list(data.pending_orders,2000)) return null;
  const currencies = [], positions = [], pending = [], seen = new Set();
  for (const row of data.currencies) {
    if (!currency(row.currency) || seen.has(row.currency) ||
        !['equity','cash_balance','available_balance','unrealized_pnl'].every(key => numeric(row[key]))) return null;
    seen.add(row.currency);
    currencies.push(pick(row,['currency','equity','cash_balance','available_balance','unrealized_pnl']));
  }
  seen.clear();
  for (const row of data.positions) {
    const identity = [row.instrument,row.side,row.margin_mode,row.currency].join('/');
    if (!SYMBOLS.has(row.instrument) || !['net','long','short'].includes(row.side) ||
        !['cross','isolated'].includes(row.margin_mode) || !currency(row.currency) ||
        row.contracts === null || !numeric(row.contracts) || Number(row.contracts) === 0 ||
        (row.side !== 'net' && Number(row.contracts) < 0) || seen.has(identity) ||
        !['average_price','mark_price','unrealized_pnl','notional_usd'].every(key => numeric(row[key])) ||
        ['average_price','mark_price'].some(key => row[key] !== null && Number(row[key]) < 0)) return null;
    seen.add(identity);
    positions.push(pick(row,['instrument','side','margin_mode','currency','contracts','average_price','mark_price','unrealized_pnl','notional_usd']));
  }
  for (const row of data.pending_orders) {
    if (!SYMBOLS.has(row.instrument) || !['buy','sell'].includes(row.side) ||
        !['net','long','short'].includes(row.position_side) || !['live','partially_filled'].includes(row.state) ||
        !numeric(row.contracts) || row.contracts === null || Number(row.contracts) <= 0 ||
        !numeric(row.filled_contracts) || row.filled_contracts === null || Number(row.filled_contracts) < 0 ||
        comparePositiveDecimals(row.filled_contracts,row.contracts) >= 0 || !numeric(row.price) ||
        (row.price !== null && Number(row.price) < 0)) return null;
    pending.push(pick(row,['instrument','side','position_side','state','contracts','filled_contracts','price']));
  }
  return {...evidence, assets:{summary:{total_equity_usd:data.summary.total_equity_usd},
    currencies,positions,pending_orders:pending}};
}

export function createAssetsSession(options) {
  return createStatusSession({...options,resource:'assets',resultValidator:verifiedAssets});
}
