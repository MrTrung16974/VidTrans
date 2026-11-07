// Firebase Cloud Messaging for the web: the browser registers its FCM token at
// /api/v1/me/devices (platform "web") so job outcomes reach it even when no
// VidTrans tab is open. /firebase-messaging-sw.js draws those pushes; while a tab
// is on screen the job socket toasts them instead.
//
// The Firebase web config is public by design (it identifies the project, it is
// not a secret). Keep it in sync with FIREBASE_CONFIG in /firebase-messaging-sw.js.
export const FIREBASE_SDK_VERSION = "11.10.0";

export const FIREBASE_CONFIG = {
  apiKey: "AIzaSyCT3W77Xi_VPt-gtLR4q3MKxJ5jawl-twM",
  authDomain: "vistrans-e0fbc.firebaseapp.com",
  databaseURL: "https://vistrans-e0fbc-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId: "vistrans-e0fbc",
  storageBucket: "vistrans-e0fbc.firebasestorage.app",
  messagingSenderId: "437010796553",
  appId: "1:437010796553:web:5e5b13031b4cde13e968d8",
  measurementId: "G-70PKFTCRT5",
};

// Firebase console → Project settings → Cloud Messaging → Web Push certificates →
// "Key pair". Empty uses the Firebase SDK's built-in key.
const VAPID_KEY = "";

const SDK = `https://www.gstatic.com/firebasejs/${FIREBASE_SDK_VERSION}`;
const DEVICE_KEY = "vidtrans.webpush.device_id";

let messagingPromise = null;
let registered = false;

function storage(action, value) {
  try {
    if (action === "get") return localStorage.getItem(DEVICE_KEY);
    if (action === "set") localStorage.setItem(DEVICE_KEY, value);
    if (action === "remove") localStorage.removeItem(DEVICE_KEY);
  } catch {
    // Private mode: the server drops the token once FCM reports it dead.
  }
  return null;
}

async function messaging() {
  messagingPromise ??= (async () => {
    if (!("serviceWorker" in navigator) || typeof Notification === "undefined" || !window.isSecureContext) return null;
    const [{ initializeApp, getApps }, fcm] = await Promise.all([
      import(`${SDK}/firebase-app.js`),
      import(`${SDK}/firebase-messaging.js`),
    ]);
    if (!await fcm.isSupported()) return null;
    const app = getApps()[0] || initializeApp(FIREBASE_CONFIG);
    const registration = await navigator.serviceWorker.register("/firebase-messaging-sw.js", { scope: "/" });
    return { fcm, instance: fcm.getMessaging(app), registration };
  })().catch(error => {
    console.warn("Web push unavailable:", error);
    return null;
  });
  return messagingPromise;
}

/** True once this browser's token is registered for the signed-in account. */
export function webPushActive() {
  return registered;
}

/**
 * Registers this browser for job pushes. Only call when permission is already
 * granted, or from a click (the browser shows the prompt only then).
 */
export async function enableWebPush(requestJson) {
  if (typeof Notification === "undefined") return false;
  if (Notification.permission === "default") {
    try {
      await Notification.requestPermission();
    } catch {
      return false;
    }
  }
  if (Notification.permission !== "granted") return false;
  const ready = await messaging();
  if (!ready) return false;
  try {
    const options = { serviceWorkerRegistration: ready.registration };
    if (VAPID_KEY) options.vapidKey = VAPID_KEY;
    const token = await ready.fcm.getToken(ready.instance, options);
    if (!token) return false;
    const device = await requestJson("/api/v1/me/devices", {
      method: "POST",
      silent: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, platform: "web", app_version: navigator.userAgent.slice(0, 64) }),
    });
    if (device?.device_id) storage("set", device.device_id);
    registered = true;
    return true;
  } catch (error) {
    console.warn("Web push registration failed:", error);
    return false;
  }
}

/** Call before the session is cleared, so the request is still authorised. */
export async function disableWebPush(requestJson) {
  registered = false;
  const deviceId = storage("get");
  storage("remove");
  if (deviceId) {
    try {
      await requestJson(`/api/v1/me/devices/${encodeURIComponent(deviceId)}`, { method: "DELETE", silent: true });
    } catch {
      // Offline sign-out: the token moves to whoever signs in next in this browser.
    }
  }
  const ready = await messaging();
  if (ready) {
    try {
      await ready.fcm.deleteToken(ready.instance);
    } catch {
      // Nothing to delete.
    }
  }
}

/** Clicks on a push while a tab is open arrive here (see the service worker). */
export function onWebPushOpen(handler) {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.addEventListener("message", event => {
    const message = event.data;
    if (message?.type === "vidtrans:open-job" && message.job_id) handler(message.job_id);
  });
}
