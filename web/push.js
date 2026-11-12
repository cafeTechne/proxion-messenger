// WebPush subscription (D4) — the missing client link for background
// notifications. The service worker already handles `push` events
// (sw.js) and the gateway already sends WebPush (webpush.py / send_web_push) to
// stored subscriptions; the only gap was that the client never subscribed. This
// fetches the gateway's VAPID public key, subscribes via the SW PushManager, and
// registers the subscription with the gateway (`subscribe_push`) so messages
// arrive with the app window closed.
//
// createPush({ getSocket }) — getSocket returns the reassignable host socket.

import { gatewayUrl } from './util.js';

// A host that a remote pod's CSS could never POST (loopback / private / mDNS). If the
// app is served from such an origin, the gateway is not publicly reachable, so
// closed-app push via the R77 webhook cannot arrive.
export function isPrivateHost(host) {
    const h = String(host || "").toLowerCase().replace(/^\[|\]$/g, "");
    if (!h) return true;
    if (h === "localhost" || h.endsWith(".local") || h.endsWith(".localhost")) return true;
    if (h === "::1" || h.startsWith("fc") || h.startsWith("fd") || h.startsWith("fe80")) return true;
    const m = h.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
    if (m) {
        const a = +m[1], b = +m[2];
        if (a === 10 || a === 127) return true;
        if (a === 192 && b === 168) return true;
        if (a === 172 && b >= 16 && b <= 31) return true;
        if (a === 169 && b === 254) return true;    // link-local
        if (a === 0) return true;
    }
    return false;
}

// Whether an invitation can reach the user with the app CLOSED:
//   'off'         — no notification permission, so no push at all.
//   'in-app-only' — served from a loopback/private origin, so the gateway is not
//                   reachable by the pod; live updates work while open, nothing when closed.
//   'on'          — public origin; closed-app push is expected to work (best-effort).
export function closedAppPushStatus({ origin, permission } = {}) {
    const o = origin || (typeof location !== "undefined" ? location.origin : "");
    const perm = permission || (typeof Notification !== "undefined" ? Notification.permission : "default");
    if (perm !== "granted") return "off";
    try {
        return isPrivateHost(new URL(o).hostname) ? "in-app-only" : "on";
    } catch {
        return "off";
    }
}

export function createPush({ getSocket }) {
    let generation = 0, enabling = null, enablingGeneration = null, disabling = null;
    let expired = null, lastRegistration = null, lastRecovery = null;
    // VAPID public key is base64url; PushManager wants a Uint8Array.
    function _urlB64ToUint8Array(b64) {
        const padding = "=".repeat((4 - (b64.length % 4)) % 4);
        const base64 = (b64 + padding).replace(/-/g, "+").replace(/_/g, "/");
        const raw = atob(base64);
        const arr = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        return arr;
    }

    // Idempotently ensure a push subscription exists and is registered with the
    // gateway. Never prompts: notification permission is asked for in context
    // (notifications.js requestNotifPermission), so while it is still undecided
    // this is a no-op. Returns true if a subscription was registered this call.
    // Safe to call on every connect.
    async function enablePush() {
        const requested = generation;
        if (disabling) { await disabling; return requested === generation ? enablePush() : false; }
        if (enabling) {
            if (enablingGeneration === requested) return enabling;
            await enabling;
            return requested === generation ? enablePush() : false;
        }
        enablingGeneration = requested;
        const pending = registerPush(requested).finally(() => { if (enabling === pending) enabling = null; });
        enabling = pending;
        return pending;
    }

    async function registerPush(requested) {
        let created = null, registered = false;
        const socket = getSocket();
        const current = () => requested === generation && getSocket() === socket
            && socket?.readyState === WebSocket.OPEN && Notification.permission === "granted";
        try {
            if (!("serviceWorker" in navigator) || !("PushManager" in window) ||
                typeof Notification === "undefined") {
                return false;
            }
            const perm = Notification.permission;
            if (perm !== "granted") return false;

            if (!current()) return false;
            const reg = await navigator.serviceWorker.ready;
            if (!current()) return false;
            const resp = await fetch(gatewayUrl("/vapid-public-key"));
            if (!resp.ok) return false;
            const { publicKey } = await resp.json();
            if (!publicKey) return false;

            if (!current()) return false;
            const key = _urlB64ToUint8Array(publicKey);
            let sub = await reg.pushManager.getSubscription();
            if (!current()) return false;
            const existing = sub?.options?.applicationServerKey;
            const bytes = existing ? new Uint8Array(existing.buffer || existing, existing.byteOffset || 0, existing.byteLength) : null;
            const subKeys = sub?.toJSON().keys || {};
            const providerExpired = expired && sub?.endpoint === expired.endpoint
                && subKeys.p256dh === expired.p256dh_b64 && subKeys.auth === expired.auth_b64;
            const keyChanged = bytes && (bytes.length !== key.length || bytes.some((b, i) => b !== key[i]));
            if (sub && ((sub.expirationTime != null && sub.expirationTime <= Date.now()) || keyChanged || providerExpired)) {
                if (!await sub.unsubscribe()) return false;
                sub = null;
            }
            if (!current()) return false;
            if (!sub) {
                sub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: key,
                });
                created = sub;
            }
            if (!current()) return false;
            const json = sub.toJSON();
            const keys = json.keys || {};
            if (current() && keys.p256dh && keys.auth) {
                lastRegistration = { endpoint: sub.endpoint, p256dh_b64: keys.p256dh, auth_b64: keys.auth, socket, generation: requested };
                socket.send(JSON.stringify({
                    cmd: "subscribe_push",
                    endpoint: sub.endpoint,
                    p256dh_b64: keys.p256dh,
                    auth_b64: keys.auth,
                }));
                registered = true;
                return true;
            }
            return false;
        } catch (_) {
            return false;
        } finally {
            if (created && !registered) { try { await created.unsubscribe(); } catch { /* retry on next connect */ } }
        }
    }

    // Only renew the exact registration the current connection just rejected.
    async function recoverExpiredSubscription(event) {
        const last = lastRegistration;
        if (!last || last.generation !== generation || last.socket !== getSocket()
            || event.endpoint !== last.endpoint || event.p256dh_b64 !== last.p256dh_b64
            || event.auth_b64 !== last.auth_b64) return false;
        const key = JSON.stringify([event.endpoint, event.p256dh_b64, event.auth_b64]);
        if (!lastRecovery || lastRecovery.socket !== last.socket || lastRecovery.generation !== generation)
            lastRecovery = { keys: new Set(), socket: last.socket, generation };
        if (lastRecovery.keys.has(key)) return false;
        lastRecovery.keys.add(key);
        expired = event;
        if (enabling) await enabling;
        if (last.generation !== generation || last.socket !== getSocket() || lastRegistration !== last) return false;
        return enablePush();
    }

    // The gateway's id for this browser's subscription (push_subscribed), so
    // turning notifications off can remove it there too.
    const SUB_ID_KEY = "proxion_push_sub_id";
    function rememberSubscription(id) {
        try { if (id) localStorage.setItem(SUB_ID_KEY, id); } catch { /* storage blocked */ }
    }

    // Stop closed-app pushes: drop the gateway's copy and the browser's own
    // subscription. Before this, turning notifications off left both in place.
    function disablePush() {
        generation++;
        if (disabling) return disabling;
        const pending = unregisterPush().finally(() => { if (disabling === pending) disabling = null; });
        disabling = pending;
        return pending;
    }

    async function unregisterPush() {
        let id = null;
        try { id = localStorage.getItem(SUB_ID_KEY); localStorage.removeItem(SUB_ID_KEY); } catch { /* ignore */ }
        const socket = getSocket();
        if (id && socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ cmd: "unsubscribe_push", subscription_id: id }));
        }
        try {
            if ("serviceWorker" in navigator) {
                const reg = await navigator.serviceWorker.ready;
                const sub = await reg.pushManager?.getSubscription();
                if (sub) await sub.unsubscribe();
            }
        } catch { /* nothing to undo */ }
    }

    return { enablePush, disablePush, rememberSubscription, recoverExpiredSubscription, _urlB64ToUint8Array };
}
