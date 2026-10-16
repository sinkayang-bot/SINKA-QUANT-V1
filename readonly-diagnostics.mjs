export const FRONTEND_VERSION = 'readonly-assets-1';
const PHASES = new Set(['signed_out', 'signed_in', 'service_pending', 'checking',
  'verified', 'expired', 'login_expired', 'forbidden', 'busy', 'unavailable',
  'timeout', 'invalid_result', 'login_unavailable', 'network_error', 'server_error']);
const FAILURE_STAGES = new Set(['token', 'request', 'response', 'validation']);

// Explicit allowlist only: never serialize the auth user or the API response.
export function diagnosticSummary(state, {signedIn, serviceConfigured}, now = Date.now()) {
  let phase = PHASES.has(state?.phase) ? state.phase : 'unknown';
  const checked = Date.parse(state?.checkedAt), until = Date.parse(state?.validUntil);
  const fresh = signedIn === true && serviceConfigured === true && phase === 'verified' &&
    state.verified === true && Number.isFinite(checked) && Number.isFinite(until) &&
    checked <= now + 5000 && until > now && until > checked && until - checked <= 30000;
  if (phase === 'verified' && !fresh) phase = Number.isFinite(until) && until <= now ? 'expired' : 'invalid_result';
  return JSON.stringify({
    app: 'SINKA QUANT', frontend_version: FRONTEND_VERSION, mode: 'demo_readonly',
    copied_at: new Date(now).toISOString(), signed_in: signedIn === true,
    status_service_configured: serviceConfigured === true, query_state: phase,
    failure_stage: !fresh && FAILURE_STAGES.has(state?.failureStage) ? state.failureStage : null,
    http_status: !fresh && Number.isInteger(state?.httpStatus) && state.httpStatus >= 100 && state.httpStatus <= 599 ? state.httpStatus : null,
    verified_at_copy: fresh, checked_at: fresh ? new Date(checked).toISOString() : null,
    valid_until: fresh ? new Date(until).toISOString() : null, trading_enabled: false,
  }, null, 2);
}
