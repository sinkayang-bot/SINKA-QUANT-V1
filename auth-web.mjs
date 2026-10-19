import {mountDemoControl} from './demo-control.mjs?v=control-1';
import {FIREBASE_CONFIG, STATUS_API_BASE_URL} from './firebase-web-config.mjs?v=status-api-1';
import {createStatusSession} from './readonly-session.mjs?v=clear-1';
import {diagnosticSummary, FRONTEND_VERSION} from './readonly-diagnostics.mjs?v=clear-1';
import {createAssetsSession} from './readonly-assets.mjs?v=clear-1';
import {renderAssets} from './assets-view.mjs?v=clear-1';

const $ = id => document.getElementById(id);
const login = $('owner-login'), logout = $('owner-logout'), refresh = $('owner-refresh');
const account = $('owner-account'), status = $('owner-status'), time = $('owner-checked');
const checkRows = ['account-config', 'balances', 'positions', 'instruments', 'pending-orders']
  .map(name => $('owner-check-' + name));
const messages = {
  signed_out: '登入後可查詢模擬帳戶唯讀連線狀態。',
  signed_in: '已登入，按「更新唯讀狀態」進行單次查詢。',
  service_pending: '已登入 Google；唯讀狀態服務尚未部署，目前無即時帳戶連線。',
  checking: '正在查詢模擬帳戶唯讀狀態…', verified: '本次唯讀查詢通過，交易功能關閉。',
  expired: '上次查詢已過期，請重新更新。', login_expired: '登入已失效，請登出後重新登入。',
  forbidden: '此帳號沒有查詢權限。', busy: '已有查詢正在處理，請稍後手動更新。',
  unavailable: '目前無法查詢，請稍後重試。', timeout: '查詢逾時，請稍後重試。',
  login_unavailable: '無法取得登入驗證，請登出後重新登入再試。',
  network_error: '無法連到唯讀服務，請確認網路後再試。',
  server_error: '唯讀服務回報錯誤，本次無法確認連線；可複製診斷摘要協助排查。',
  invalid_result: '結果未通過完整核對，目前無法確認連線。',
};
let auth, sdk, authBusy = false;
const demoControl=mountDemoControl({getUser:()=>auth?.currentUser});
let assetsSession;
const diagnosticOutput = $('owner-diagnostic-output'), diagnosticNotice = $('owner-diagnostic-notice');
if ($('owner-version')) $('owner-version').textContent = '介面版本：' + FRONTEND_VERSION;
const session = createStatusSession({baseUrl: STATUS_API_BASE_URL, onChange: state => {
  if(!state.verified && $('owner-validity-countdown'))$('owner-validity-countdown').textContent='';
  if (diagnosticOutput) { diagnosticOutput.value = ''; diagnosticOutput.hidden = true; }
  if (diagnosticNotice) diagnosticNotice.textContent = '';
  status.textContent = messages[state.phase] || messages.unavailable;
  status.className = state.verified ? 'note up' : 'note';
  // Only the complete validated response may mark any individual check passed.
  const checkText = state.verified ? '通過' : state.phase === 'expired' ? '已過期' :
    state.phase === 'checking' ? '等待結果' :
    ['signed_out', 'signed_in', 'service_pending'].includes(state.phase) ? '尚未查詢' : '未確認';
  for (const row of checkRows) {
    if (!row) continue; // Older cached HTML still supports the summary status.
    row.textContent = checkText;
    row.className = state.verified ? 'up' : '';
  }
  refresh.textContent = state.phase === 'checking' ? '正在查詢…' : state.verified ? '結果有效中' : '更新唯讀狀態';
  refresh.setAttribute('aria-busy', String(state.phase === 'checking'));
  syncRefreshButtons();
  time.textContent = state.verified ? '檢查完成：' + new Date(state.checkedAt).toLocaleString('zh-TW', {timeZone: 'Asia/Taipei'}) + '（台灣）；30 秒內有效。' : '';
}});
assetsSession = createAssetsSession({baseUrl:STATUS_API_BASE_URL,onChange:state=>{
  if(!state.verified && $('asset-validity-countdown'))$('asset-validity-countdown').textContent='';
  if (diagnosticOutput) {diagnosticOutput.value='';diagnosticOutput.hidden=true;}
  if (diagnosticNotice) diagnosticNotice.textContent='';
  renderAssets(state,messages); syncRefreshButtons();
}});
function syncRefreshButtons() {
  const state=session.getState(), assetState=assetsSession?.getState();
  const busy=authBusy || state.phase==='checking' || assetState?.phase==='checking';
  refresh.disabled=!auth?.currentUser || !STATUS_API_BASE_URL || busy || state.verified;
  const assetRefresh=$('asset-refresh');
  if (assetRefresh) {
    assetRefresh.disabled=!auth?.currentUser || !STATUS_API_BASE_URL || busy || assetState?.verified;
    assetRefresh.textContent=assetState?.phase==='checking'?'正在讀取資產…':assetState?.verified?'資產結果有效中':'更新模擬帳戶資產';
    assetRefresh.setAttribute('aria-busy',String(assetState?.phase==='checking'));
  }
}
function renderUser(user) {
  if(!user)demoControl?.clear();
  account.textContent = user ? 'Google 已登入' : '尚未登入';
  login.hidden = Boolean(user); logout.hidden = !user;
  session.setUser(user);
  assetsSession.setUser(user);
}
function loginError(error) {
  const code = error?.code;
  status.textContent = code === 'auth/popup-blocked' ? '瀏覽器擋住登入視窗，請允許彈出視窗後重試。' :
    code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request' ? '登入已取消。' :
    code === 'auth/unauthorized-domain' ? '網站登入網域尚未通過設定，請稍後再試。' : 'Google 登入未完成，請重試。';
}
login.addEventListener('click', async () => {
  if (!auth || authBusy) return;
  authBusy = true; login.disabled = true;
  try {
    const provider = new sdk.GoogleAuthProvider();
    provider.setCustomParameters({prompt: 'select_account'});
    await sdk.signInWithPopup(auth, provider);
  } catch (error) { loginError(error); }
  finally {
    authBusy = false; login.disabled = false; session.checkExpiry();
    syncRefreshButtons();
  }
});
logout.addEventListener('click', async () => {
  if (!auth || authBusy) return;
  authBusy = true; logout.disabled = true;
  session.setUser(null); // Clear status and abort before waiting for sign-out.
  assetsSession.setUser(null);
  try { await sdk.signOut(auth); renderUser(null); }
  catch { status.textContent = '登出未完成，已清除查詢結果，請再次按登出。'; }
  finally { authBusy = false; logout.disabled = false; }
});
refresh.addEventListener('click', () => {if(assetsSession.getState().phase!=='checking')session.refresh();});
$('asset-refresh')?.addEventListener('click',()=>{if(session.getState().phase!=='checking')assetsSession.refresh();});
$('owner-clear-results')?.addEventListener('click',()=>{
  session.clearResults();assetsSession.clearResults();updateValidityCountdown();
  if(diagnosticNotice)diagnosticNotice.textContent='已清除本次查詢結果並取消尚未完成的查詢；登入狀態保留。';
});
$('asset-full-precision')?.addEventListener('change',()=>renderAssets(assetsSession.getState(),messages));
$('asset-hide-details')?.addEventListener('change',()=>renderAssets(assetsSession.getState(),messages));
$('owner-copy-diagnostic')?.addEventListener('click', async () => {
  const context={
    signedIn: Boolean(auth?.currentUser), serviceConfigured: Boolean(STATUS_API_BASE_URL),
  };
  const snapshot = JSON.stringify({...JSON.parse(diagnosticSummary(session.getState(),context)),
    assets_query:JSON.parse(diagnosticSummary(assetsSession.getState(),context))},null,2);
  diagnosticOutput.value = snapshot; diagnosticOutput.hidden = false;
  try {
    await navigator.clipboard.writeText(snapshot);
    diagnosticNotice.textContent = '診斷摘要已複製。這是複製當下的狀態快照。';
  } catch {
    diagnosticNotice.textContent = '瀏覽器未允許複製，請選取下方摘要手動複製。';
  }
});
function checkExpiry(){session.checkExpiry();assetsSession.checkExpiry();}
let validityClock=null;
function updateValidityCountdown(){
  for(const [id,controller,label] of [['owner-validity-countdown',session,'連線結果'],['asset-validity-countdown',assetsSession,'資產資料']]){
    const milliseconds=controller.getRemainingMs(), element=$(id);
    if(element)element.textContent=milliseconds>0?label+'尚有 '+Math.ceil(milliseconds/1000)+' 秒有效；到期會清除。':'';
  }
}
function stopValidityClock(){if(validityClock!==null)clearInterval(validityClock);validityClock=null;}
function startValidityClock(){updateValidityCountdown();if(validityClock===null && document.visibilityState!=='hidden')validityClock=setInterval(updateValidityCountdown,1000);}
document.addEventListener('visibilitychange',()=>{checkExpiry();document.visibilityState==='hidden'?stopValidityClock():startValidityClock();});
window.addEventListener('focus',()=>{checkExpiry();updateValidityCountdown();});
window.addEventListener('pagehide',()=>{stopValidityClock();session.setUser(null);assetsSession.setUser(null);updateValidityCountdown();});
window.addEventListener('pageshow',()=>{if(auth)renderUser(auth.currentUser);startValidityClock();});
startValidityClock(); // UI clock only; never refreshes a token or calls an API.

try {
  const [appSdk, authSdk] = await Promise.all([
    import('https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js'),
    import('https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js'),
  ]);
  sdk = authSdk;
  // Initialize with memory-only persistence from the outset, not localStorage.
  auth = sdk.initializeAuth(appSdk.initializeApp(FIREBASE_CONFIG, 'sinka-owner-login'), {
    persistence: sdk.inMemoryPersistence, popupRedirectResolver: sdk.browserPopupRedirectResolver,
  });
  sdk.onAuthStateChanged(auth, user => {renderUser(user); login.disabled = false;}, () => {
    session.setUser(null); assetsSession.setUser(null); status.textContent = '登入狀態無法確認，請重新整理後再試。';
  });
} catch {
  status.textContent = 'Google 登入元件載入失敗，請重新整理後再試。';
  login.disabled = true;
}
