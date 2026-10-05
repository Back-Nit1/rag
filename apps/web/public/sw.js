// Metis service worker: app-shell cache plus web-push display.
//
// Deliberately small. It does not cache API responses: a stale task list that
// looks live is worse than an honest offline banner, and the app already keeps
// an unsent-capture outbox of its own.
const SHELL = "metis-shell-v1";
const SHELL_URLS = ["/", "/index.html", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) return;
  // Network first, shell cache only as the offline fallback for navigation.
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/index.html").then((r) => r || Response.error())));
  }
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: "Metis", body: "You have a new reminder." };
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || "Metis", {
      body: payload.body || "",
      tag: payload.dedupe_key || undefined,
      data: { url: payload.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      const open = clients.find((c) => c.url.includes(self.location.origin));
      if (open) return open.focus();
      return self.clients.openWindow(url);
    }),
  );
});
