// VidTrans web push (Firebase Cloud Messaging). The server sends browsers
// data-only messages ({type, job_id, status, title, body}); this worker draws
// them, and opens the job when the notification is clicked.
// Keep FIREBASE_CONFIG and the SDK version in sync with /js/web-push.js.
importScripts(
  "https://www.gstatic.com/firebasejs/11.10.0/firebase-app-compat.js",
  "https://www.gstatic.com/firebasejs/11.10.0/firebase-messaging-compat.js",
);

const FIREBASE_CONFIG = {
  apiKey: "AIzaSyCT3W77Xi_VPt-gtLR4q3MKxJ5jawl-twM",
  authDomain: "vistrans-e0fbc.firebaseapp.com",
  databaseURL: "https://vistrans-e0fbc-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId: "vistrans-e0fbc",
  storageBucket: "vistrans-e0fbc.firebasestorage.app",
  messagingSenderId: "437010796553",
  appId: "1:437010796553:web:5e5b13031b4cde13e968d8",
  measurementId: "G-70PKFTCRT5",
};

firebase.initializeApp(FIREBASE_CONFIG);
const messaging = firebase.messaging();

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

messaging.onBackgroundMessage(async payload => {
  const data = payload.data || {};
  if (!data.title) return;
  // A VidTrans tab on screen already toasts this from the job socket.
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  if (windows.some(client => client.visibilityState === "visible")) return;
  await self.registration.showNotification(data.title, {
    body: data.body || "",
    // Same tag as the page's own notification, so one outcome is shown once.
    tag: data.job_id ? `${data.job_id}:${data.status || ""}` : undefined,
    icon: "/assets/vidtrans-mark.svg",
    badge: "/assets/vidtrans-mark.svg",
    data,
  });
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const jobId = event.notification.data?.job_id;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = windows.find(client => new URL(client.url).origin === self.location.origin);
    if (open) {
      await open.focus();
      if (jobId) open.postMessage({ type: "vidtrans:open-job", job_id: jobId });
      return;
    }
    await self.clients.openWindow(jobId ? `/?job=${encodeURIComponent(jobId)}#jobs` : "/");
  })());
});
