const CACHE = "proxion-shell-v68";
const SHELL = [
  "/",
  "/index.html",
  "/style.css",
  "/main.js",
  "/util.js",
  "/filetransfer.js",
  "/voice.js",
  "/notifications.js",
  "/onboarding.js",
  "/reactions.js",
  "/pins.js",
  "/media.js",
  "/modals.js",
  "/profile.js",
  "/edit.js",
  "/mute.js",
  "/mentions.js",
  "/rooms.js",
  "/address.js",
  "/typing.js",
  "/members.js",
  "/friend-requests.js",
  "/e2e-status.js",
  "/status-banners.js",
  "/connection.js",
  "/rendering.js",
  "/view.js",
  "/invite.js",
  "/focus-trap.js",
  "/device-cert.js",
  "/pairing.js",
  "/dmhistory.js",
  "/push.js",
  "/states.js",
  "/auth.js",
  "/pod.js",
  "/solid-authn.bundle.js",
  "/manifest.json",
  "/icons/icon-192.svg",
  "/icons/icon-512.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon-180.png",
];

// On localhost, skip all caching so development changes are always live.
const IS_LOCAL = self.location.hostname === "localhost" ||
                 self.location.hostname === "127.0.0.1";

self.addEventListener("install", (e) => {
  if (IS_LOCAL) { self.skipWaiting(); return; }
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // Never intercept WebSocket upgrades or gateway API calls
  if (url.protocol === "ws:" || url.protocol === "wss:") return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws")) return;

  // On localhost: always go to the network so code changes are immediate.
  if (IS_LOCAL) return;

  // Network-first for navigations (so auth redirects work); cache fallback
  if (e.request.mode === "navigate") {
    e.respondWith(
      fetch(e.request)
        .then((r) => {
          const clone = r.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone));
          return r;
        })
        .catch(() => caches.match("/index.html"))
    );
    return;
  }

  // Cache-first for shell assets
  e.respondWith(
    caches.match(e.request).then(
      (cached) =>
        cached ||
        fetch(e.request).then((r) => {
          if (r.ok) {
            const clone = r.clone();
            caches.open(CACHE).then((c) => c.put(e.request, clone));
          }
          return r;
        })
    )
  );
});

// R13.9: Web Push notification support
self.addEventListener("push", (event) => {
  let data = { title: "Proxion", body: "New message" };
  try { data = event.data ? event.data.json() : data; } catch (_) {}
  const threadId = data.thread_id || "";
  event.waitUntil(
    self.registration.showNotification(data.title || "Proxion", {
      body: data.body || "",
      // PNG, not SVG — several platforms ignore SVG notification icons.
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      // Per-thread tag so different conversations don't collapse into one.
      tag: threadId ? ("proxion-" + threadId) : "proxion-msg",
      renotify: true,
      data: { thread_id: threadId },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const threadId = (event.notification.data && event.notification.data.thread_id) || "";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      const c = clients.find((c) => c.url.includes(self.location.origin) && "focus" in c);
      if (c) {
        // Focus the open app and tell it which conversation to open.
        if (threadId) { try { c.postMessage({ type: "navigate-thread", thread_id: threadId }); } catch (_) {} }
        return c.focus();
      }
      // Cold start: carry the thread in the URL so the app opens it on load.
      if (self.clients.openWindow) return self.clients.openWindow(threadId ? ("/?thread=" + encodeURIComponent(threadId)) : "/");
    })
  );
});
