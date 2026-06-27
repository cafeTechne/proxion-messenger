// WebPush subscription (D4) — the missing client link for background
// notifications. The service worker already handles `push` events
// (sw.js) and the gateway already sends WebPush (webpush.py / send_web_push) to
// stored subscriptions; the only gap was that the client never subscribed. This
// fetches the gateway's VAPID public key, subscribes via the SW PushManager, and
// registers the subscription with the gateway (`subscribe_push`) so messages
// arrive with the app window closed.
//
// createPush({ getSocket }) — getSocket returns the reassignable host socket.

export function createPush({ getSocket }) {
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
    // gateway. Requests notification permission if still undecided. Returns true
    // if a subscription was registered this call. Safe to call on every connect.
    async function enablePush() {
        try {
            if (!("serviceWorker" in navigator) || !("PushManager" in window) ||
                typeof Notification === "undefined") {
                return false;
            }
            let perm = Notification.permission;
            if (perm === "default") perm = await Notification.requestPermission();
            if (perm !== "granted") return false;

            const reg = await navigator.serviceWorker.ready;
            const resp = await fetch("/vapid-public-key");
            if (!resp.ok) return false;
            const { publicKey } = await resp.json();
            if (!publicKey) return false;

            let sub = await reg.pushManager.getSubscription();
            if (!sub) {
                sub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: _urlB64ToUint8Array(publicKey),
                });
            }
            const json = sub.toJSON();
            const keys = json.keys || {};
            const socket = getSocket();
            if (socket && socket.readyState === WebSocket.OPEN && keys.p256dh && keys.auth) {
                socket.send(JSON.stringify({
                    cmd: "subscribe_push",
                    endpoint: sub.endpoint,
                    p256dh_b64: keys.p256dh,
                    auth_b64: keys.auth,
                }));
                return true;
            }
            return false;
        } catch (_) {
            return false;
        }
    }

    return { enablePush, _urlB64ToUint8Array };
}
