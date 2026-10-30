const CACHE = "proxion-shell-v259";
const SHELL = [
  "./",
  "index.html",
  "style.css",
  "main.js",
  "i18n.js",
  "theme.js",
  "theme-boot.js",
  "locales/en.json",
  "locales/ja.json",
  "locales/zh-hans.json",
  "locales/zh-hant.json",
  "locales/ko.json",
  "locales/de.json",
  "locales/fr.json",
  "locales/es.json",
  "locales/pt.json",
  "locales/ru.json",
  "locales/it.json",
  "locales/pl.json",
  "locales/tr.json",
  "locales/vi.json",
  "locales/id.json",
  "locales/ar.json",
  "util.js",
  "filetransfer.js",
  "e2e-secondary.js",
  "voice.js",
  "notifications.js",
  "onboarding.js",
  "reactions.js",
  "pins.js",
  "media.js",
  "modals.js",
  "profile.js",
  "edit.js",
  "mute.js",
  "account-sync.js",
  "mentions.js",
  "autocomplete.js",
  "rooms.js",
  "address.js",
  "typing.js",
  "members.js",
  "friend-requests.js",
  "e2e-status.js",
  "status-banners.js",
  "connection.js",
  "transport.js",
  "podtransport.js",
  "web-account.js",
  "webdm.js",
  "dmkey.js",
  "webpresence.js",
  "webcalls.js",
  "webjoin.js",
  "webrooms.js",
  "clientid.jsonld",
  "rendering.js",
  "view.js",
  "invite.js",
  "focus-trap.js",
  "wayfinding.js",
  "tablock.js",
  "settings-panel.js",
  "a11y.js",
  "device-cert.js",
  "pairing.js",
  "recovery.js",
  "gifs.js",
  "media-resize.js",
  "gifmotion.js",
  "dropzone.js",
  "emoji.js",
  "saved.js",
  "blocks.js",
  "send-status.js",
  "podhealth.js",
  "sendqueue.js",
  "podqueue.js",
  "notify.js",
  "polls.js",
  "room-emoji.js",
  "meme.js",
  "dialogs.js",
  "menu.js",
  "errors.js",
  "dmhistory.js",
  "dmtext.js",
  "push.js",
  "states.js",
  "icons.js",
  "ssrf.js",
  "dmsig.js",
  "auth.js",
  "pod.js",
  "acl.js",
  "longchat.js",
  "typeindex.js",
  "roomdesc.js",
  "ldn.js",
  "callsec.js",
  "callquality.js",
  "devices.js",
  "callstats.js",
  "solidchat.js",
  "solidchat-ui.js",
  "solid-authn.bundle.js",
  "e2e.js",
  "identity.js",
  "connectivity.js",
  "sw-register.js",
  "manifest.json",
  "icons/icon-192.svg",
  "icons/icon-512.svg",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/apple-touch-icon-180.png",
  "icons/maskable-512.svg",
  "vendor/qrcode.min.js",
  "vendor/jsQR.min.js",
];

// On localhost, skip all caching so development changes are always live.
const IS_LOCAL = self.location.hostname === "localhost" ||
                 self.location.hostname === "127.0.0.1";

// Runtime-cache only same-origin static assets. Never persist authenticated pod
// GETs or dynamic gateway responses, and keep the cache from growing unbounded.
const STATIC_ASSET = /\.(?:js|jsonld|css|svg|png|ico|woff2|json|webmanifest)$/;

// The page itself must be cached for the worker to be useful offline; the rest
// is cached one by one, so a single asset that 404s (or a gateway that serves
// it with the wrong type) cannot fail the whole install. addAll is atomic and
// one miss used to leave the app with no worker at all: no offline shell, and
// no push over the tunnel.
const SHELL_REQUIRED = ["./", "index.html", "style.css", "main.js"];

// Install fetches bypass the HTTP cache: a CDN max-age would otherwise let the
// new cache hold a new index.html next to a stale main.js.
const fresh = (u) => new Request(new URL(u, self.location).href, { cache: "reload" });

self.addEventListener("install", (e) => {
  if (IS_LOCAL) { self.skipWaiting(); return; }
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL_REQUIRED.map(fresh)).then(() =>
      Promise.allSettled(SHELL.filter((u) => !SHELL_REQUIRED.includes(u)).map((u) => c.add(fresh(u))))
    )).then(() => self.skipWaiting())
  );
});

// A page must never run new HTML with old scripts. Navigations go to the
// network (so auth redirects work), but the shell assets are cache-first from
// this worker's cache, which predates a deploy until the new worker activates.
// So when a navigation's HTML differs from the cached shell, that page's
// assets come from the network as well. Keyed by client id, bounded.
const FRESH_CLIENTS = new Set();
const MAX_FRESH_CLIENTS = 64;

function markFresh(clientId) {
  if (!clientId) return;
  if (FRESH_CLIENTS.size >= MAX_FRESH_CLIENTS) FRESH_CLIENTS.delete(FRESH_CLIENTS.values().next().value);
  FRESH_CLIENTS.add(clientId);
}

// The page's memory cache would otherwise keep reusing an asset under its
// CDN max-age without asking this worker again, so a page could still pair new
// HTML with old scripts. What the worker serves is marked for revalidation,
// which only costs a cache lookup here.
function revalidate(r) {
  if (!r || !r.headers || r.type === "opaque" || r.type === "opaqueredirect") return r;
  try {
    const headers = new Headers(r.headers);
    headers.set("Cache-Control", "no-cache");
    return new Response(r.body, { status: r.status, statusText: r.statusText, headers });
  } catch (_) {
    return r;
  }
}

async function matchesShell(response) {
  try {
    const cached = await (await caches.open(CACHE)).match("index.html");
    if (!cached || !response.ok) return false;
    const [a, b] = await Promise.all([response.clone().text(), cached.text()]);
    return a === b;
  } catch (_) {
    return false;
  }
}

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

  // Never touch cross-origin requests (e.g. authenticated pod GETs via
  // solidSession.fetch) or non-GET methods: caching them would serve stale
  // realtime reads and leak authenticated pod responses into shared storage.
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;

  // On localhost: always go to the network so code changes are immediate.
  if (IS_LOCAL) return;

  // Network-first for navigations (so auth redirects work); the offline
  // fallback is the shell cached at install, the same generation as its
  // assets. A navigation is never written to the cache: that would pair new
  // HTML with old assets offline (and could persist an OIDC callback URL).
  if (e.request.mode === "navigate") {
    e.respondWith(
      fetch(e.request)
        .then(async (r) => {
          if (!(await matchesShell(r))) markFresh(e.resultingClientId);
          return r;
        })
        .catch(() => caches.match("index.html"))
    );
    return;
  }

  // A page whose HTML is newer than this cache: its assets from the network,
  // revalidated, and not stored (the cache stays one consistent generation).
  if (FRESH_CLIENTS.has(e.clientId)) {
    e.respondWith(fetch(e.request, { cache: "no-cache" }).catch(() => caches.match(e.request)).then(revalidate));
    return;
  }

  // Cache-first for shell assets
  e.respondWith(
    caches.match(e.request).then(
      (cached) =>
        cached ||
        fetch(e.request).then((r) => {
          // Only ever persist same-origin static assets, not arbitrary r.ok
          // responses (which would grow the cache without bound).
          if (r.ok && STATIC_ASSET.test(url.pathname)) {
            const clone = r.clone();
            caches.open(CACHE).then((c) => c.put(e.request, clone));
          }
          return r;
        })
    ).then(revalidate)
  );
});

// i18n mirror (G4): the SW can't import i18n.js or read localStorage, so read
// the locale's push strings from the IndexedDB copy i18n.js writes on boot.
// Falls back to English if the app has never run since install.
function _i18nPush() {
  return new Promise((resolve) => {
    try {
      const req = indexedDB.open("proxion-i18n", 1);
      req.onupgradeneeded = () => { try { req.result.createObjectStore("kv"); } catch (_) {} };
      req.onsuccess = () => {
        try {
          const g = req.result.transaction("kv", "readonly").objectStore("kv").get("push");
          g.onsuccess = () => resolve(g.result || null);
          g.onerror = () => resolve(null);
        } catch (_) { resolve(null); }
      };
      req.onerror = () => resolve(null);
    } catch (_) { resolve(null); }
  });
}

// R13.9: Web Push notification support
self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch (_) {}
    const i18n = await _i18nPush();
    const threadId = data.thread_id || "";
    const isInvite = data.type === "invite";
    const isMissedCall = data.type === "missed_call";
    await self.registration.showNotification(
      data.title || "Proxion",
      {
        // Server-provided body wins; otherwise the localized default. Invitations and
        // missed calls carry no content (privacy); the body comes from the i18n mirror.
        body: data.body
          || (isMissedCall ? ((i18n && i18n.missedCall) || "Missed call")
              : isInvite ? ((i18n && i18n.newInvite) || "New chat invitation")
                         : ((i18n && i18n.newMessage) || "New message")),
        // PNG, not SVG — several platforms ignore SVG notification icons.
        // Relative so they resolve under the SW scope at any base path.
        icon: "icons/icon-192.png",
        badge: "icons/icon-192.png",
        // Per-thread tag so different conversations don't collapse into one;
        // invitations get their own tag so they never merge with messages.
        tag: isMissedCall ? "proxion-missed-call" : isInvite ? "proxion-invite" : (threadId ? ("proxion-" + threadId) : "proxion-msg"),
        renotify: true,
        data: { thread_id: threadId, type: data.type || "message" },
      }
    );
  })());
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
      if (self.clients.openWindow) return self.clients.openWindow(threadId ? ("./?thread=" + encodeURIComponent(threadId)) : "./");
    })
  );
});
