import {FIREBASE_CONFIG, STATUS_API_BASE_URL} from './firebase-web-config.mjs?v=status-api-1';
import {createStatusSession} from './readonly-session.mjs';

const $ = id => document.getElementById(id);
const login = $('owner-login'), logout = $('owner-logout'), refresh = $('owner-refresh');
const account = $('owner-account'), status = $('owner-status'), time = $('owner-checked');
const messages = {
  signed_out: '登入後可查詢模擬帳戶唯讀連線狀態。',
  signed_in: '已登入，按「更新唯讀狀態」進行單次查詢。',
  service_pending: '已登入 Google；唯讀狀態服務尚未部署，目前無即時帳戶連線。',
  checking: '正在查詢模擬帳戶唯讀狀態…', verified: '本次唯讀查詢通過，交易功能關閉。',
  expired: '上次查詢已過期，請重新更新。', login_expired: '登入已失效，請登出後重新登入。',
  forbidden: '此帳號沒有查詢權限。', busy: '已有查詢正在處理，請稍後手動更新。',
  unavailable: '目前無法查詢，請稍後重試。', timeout: '查詢逾時，請稍後重試。',
  invalid_result: '結果未通過完整核對，目前無法確認連線。',
};
let auth, sdk, authBusy = false;
const session = createStatusSession({baseUrl: STATUS_API_BASE_URL, onChange: state => {
  status.textContent = messages[state.phase] || messages.unavailable;
  status.className = state.verified ? 'note up' : 'note';
  refresh.disabled = !auth?.currentUser || !STATUS_API_BASE_URL || state.phase === 'checking' || authBusy;
  time.textContent = state.verified ? '檢查完成：' + new Date(state.checkedAt).toLocaleString('zh-TW', {timeZone: 'Asia/Taipei'}) + '（台灣）；30 秒內有效。' : '';
}});
function renderUser(user) {
  account.textContent = user ? 'Google 已登入' : '尚未登入';
  login.hidden = Boolean(user); logout.hidden = !user;
  session.setUser(user);
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
    refresh.disabled = !auth.currentUser || !STATUS_API_BASE_URL || session.getState().phase === 'checking';
  }
});
logout.addEventListener('click', async () => {
  if (!auth || authBusy) return;
  authBusy = true; logout.disabled = true;
  session.setUser(null); // Clear status and abort before waiting for sign-out.
  try { await sdk.signOut(auth); renderUser(null); }
  catch { status.textContent = '登出未完成，已清除查詢結果，請再次按登出。'; }
  finally { authBusy = false; logout.disabled = false; }
});
refresh.addEventListener('click', () => session.refresh());
document.addEventListener('visibilitychange', () => session.checkExpiry());
window.addEventListener('focus', () => session.checkExpiry());
window.addEventListener('pagehide', () => session.setUser(null));
window.addEventListener('pageshow', () => { if (auth) renderUser(auth.currentUser); });

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
    session.setUser(null); status.textContent = '登入狀態無法確認，請重新整理後再試。';
  });
} catch {
  status.textContent = 'Google 登入元件載入失敗，請重新整理後再試。';
  login.disabled = true;
}
