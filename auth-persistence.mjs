const preferenceKey = 'sinka-remember-login';

export function rememberLogin(storage) {
  try { return storage?.getItem(preferenceKey) !== 'false'; }
  catch { return true; }
}

export function loginPersistence(sdk, remember) {
  return remember ? [sdk.browserLocalPersistence, sdk.inMemoryPersistence] : sdk.inMemoryPersistence;
}

export async function changeLoginPersistence({sdk, auth, remember, storage}) {
  await sdk.setPersistence(auth, remember ? sdk.browserLocalPersistence : sdk.inMemoryPersistence);
  // Store only the user's preference. Firebase owns credential persistence.
  try { storage?.setItem(preferenceKey, String(remember)); } catch {}
}
