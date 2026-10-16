// Public Firebase Web App identifiers copied from the sinka-quant console.
// These are not OKX credentials. Authorization is enforced by the backend UID.
export const FIREBASE_CONFIG = Object.freeze({
  apiKey: 'AIzaSyBEl21eE7fI4Ojhyx5DN4jTKmhKmxK8R0E',
  authDomain: 'sinka-quant.firebaseapp.com',
  projectId: 'sinka-quant',
  appId: '1:361301529939:web:656723ad04a3c8075bfda6',
});
// Dedicated read-only Cloud Run API deployed and denial probes verified.
// No user-entered URL is accepted for sending Firebase bearer tokens.
export const STATUS_API_BASE_URL = 'https://sinka-okx-readonly-status-361301529939.asia-east1.run.app';
