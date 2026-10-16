const CHECKS = ['account_config', 'balances', 'positions', 'instruments', 'pending_orders'];

export function statusEndpoint(base) {
  if (!base) return null;
  const url = new URL(base);
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.run.app') || url.port ||
      url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Invalid status service origin');
  }
  return url.origin + '/api/okx/status';
}

export function verifiedResult(data, now) {
  if (!data || data.mode !== 'demo_readonly' || data.okx_connection_verified !== true ||
      data.api_permissions_readonly !== true || data.trading_enabled !== false ||
      !data.checks || Object.keys(data.checks).length !== CHECKS.length ||
      !CHECKS.every(key => data.checks[key] === true) ||
      typeof data.checked_at !== 'string' || typeof data.valid_until !== 'string') return null;
  const checked = Date.parse(data.checked_at), until = Date.parse(data.valid_until);
  if (!Number.isFinite(checked) || !Number.isFinite(until) || checked > now + 5000 ||
      until <= now || until <= checked || until - checked > 30000) return null;
  // A tolerated server clock lead must not extend local evidence past 30 seconds.
  const expiresAt = Math.min(until, now + 30000);
  return {checkedAt: data.checked_at, validUntil: new Date(expiresAt).toISOString(), expiresAt};
}

export function createStatusSession({baseUrl = null, fetcher = fetch, onChange,
    now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout}) {
  const endpoint = statusEndpoint(baseUrl);
  let user = null, generation = 0, pending = null, expiryTimer = null;
  let state = {phase: 'signed_out', verified: false};
  const publish = (phase, extra = {}) => {
    state = {phase, verified: phase === 'verified', ...extra};
    onChange({...state});
  };
  const clearExpiry = () => { if (expiryTimer !== null) clearTimer(expiryTimer); expiryTimer = null; };
  function setUser(value) {
    generation++;
    pending?.abort(); pending = null;
    clearExpiry(); user = value;
    publish(user ? (endpoint ? 'signed_in' : 'service_pending') : 'signed_out');
  }
  function checkExpiry() {
    if (state.verified && now() >= state.expiresAt) {
      clearExpiry(); publish('expired');
    }
  }
  async function refresh() {
    checkExpiry();
    if (!user || pending) return;
    if (state.verified) return; // Reuse still-valid evidence without requesting a token.
    if (!endpoint) { publish('service_pending'); return; }
    clearExpiry();
    const version = ++generation, selected = user;
    const controller = new AbortController(); pending = controller;
    let requestTimer, failureStage = 'token', httpStatus = null;
    const deadline = new Promise((resolve, reject) => {
      requestTimer = setTimer(() => {controller.abort(); reject(new Error('timeout'));}, 40000);
    });
    publish('checking');
    try {
      const token = await Promise.race([selected.getIdToken(), deadline]);
      if (version !== generation || controller.signal.aborted) return;
      failureStage = 'request';
      const response = await Promise.race([fetcher(endpoint, {
        method: 'GET', headers: {Authorization: 'Bearer ' + token},
        cache: 'no-store', credentials: 'omit', redirect: 'error',
        referrerPolicy: 'no-referrer', signal: controller.signal,
      }), deadline]);
      if (version !== generation || controller.signal.aborted) return;
      failureStage = 'response';
      if (Number.isInteger(response.status) && response.status >= 100 && response.status <= 599) httpStatus = response.status;
      if (!response.ok) {
        publish(response.status === 401 ? 'login_expired' : response.status === 403 ? 'forbidden' :
          response.status === 429 ? 'busy' : response.status === 504 ? 'timeout' :
          response.status >= 500 ? 'server_error' : 'unavailable', {failureStage, httpStatus});
        return;
      }
      const data = await Promise.race([response.json(), deadline]);
      if (version !== generation || controller.signal.aborted) return;
      failureStage = 'validation';
      const clean = verifiedResult(data, now());
      if (!clean) { publish('invalid_result', {failureStage, httpStatus}); return; }
      publish('verified', clean);
      expiryTimer = setTimer(() => { if (version === generation) checkExpiry(); }, Math.min(30000, clean.expiresAt - now()));
    } catch {
      if (version === generation) publish(controller.signal.aborted ? 'timeout' :
        failureStage === 'token' ? 'login_unavailable' : failureStage === 'request' ? 'network_error' :
        'invalid_result', {failureStage, httpStatus});
    } finally {
      clearTimer(requestTimer);
      if (pending === controller) pending = null;
      // A stalled token refresh must not leave a verified indicator behind.
      if (version === generation && state.phase === 'checking') publish('timeout', {failureStage, httpStatus});
    }
  }
  return {setUser, refresh, checkExpiry, getState: () => {checkExpiry(); return {...state};}};
}
