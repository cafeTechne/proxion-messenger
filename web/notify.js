// notify.js — real-time resource watching via the Solid Notifications Protocol
// v0.3.0 (PLAN_ROUND_70 Track A), with a polling fallback.
//
// We speak the protocol DIRECTLY rather than via @inrupt/solid-client-notifications:
// verified live, that lib (v3) looks for an old-style negotiation gateway and
// reports CSS 7 as "not supporting notifications", while CSS actually advertises a
// WebSocketChannel2023 subscription service in its storage description (the v0.3
// model). The direct flow needs only session.fetch + the native WebSocket, no
// bundle, and works against CSS.
//
// Design: polling is the BASELINE (works against any server); a WebSocket
// subscription UPGRADES it. Once the socket opens we stop polling; if discovery,
// subscription, or the socket fail, polling carries on. The spec does not
// guarantee a server offers notifications, so the fallback is required. `onChange`
// is a nudge called with no args; the caller re-reads and dedups.
import { solidSession } from './auth.js';

const NS = 'http://www.w3.org/ns/solid/notifications#';
const WS_CHANNEL = NS + 'WebSocketChannel2023';
const WEBHOOK_CHANNEL = NS + 'WebhookChannel2023';
const CHANNEL_TYPE = NS + 'channelType';
const STORAGE_DESCRIPTION = 'http://www.w3.org/ns/solid/terms#storageDescription';

function _firstLinkByRel(linkHeader, rel) {
    // Parse an HTTP Link header for <url>; rel="rel".
    for (const part of String(linkHeader || '').split(',')) {
        const m = part.match(/<([^>]+)>\s*;\s*rel\s*=\s*"?([^";]+)"?/i);
        if (m && m[2].split(/\s+/).includes(rel)) return m[1];
    }
    return null;
}

function _nodes(json) {
    if (!json) return [];
    if (Array.isArray(json)) return json;
    if (Array.isArray(json['@graph'])) return json['@graph'];
    return [json];
}

/**
 * The subscription-service URL for a given channel type on a resource, discovered via
 * its storage description, or null if the server advertises none of that type.
 */
async function _discoverChannelService(resourceUrl, channelType) {
    let descUrl = null;
    try {
        const head = await solidSession.fetch(resourceUrl, { method: 'HEAD' });
        descUrl = _firstLinkByRel(head.headers.get('link'), STORAGE_DESCRIPTION);
    } catch { /* fall through */ }
    if (!descUrl) return null;
    let json;
    try {
        const res = await solidSession.fetch(descUrl, { headers: { Accept: 'application/ld+json' } });
        if (!res.ok) return null;
        json = await res.json();
    } catch { return null; }
    for (const node of _nodes(json)) {
        if (!node || typeof node !== 'object') continue;
        const ct = node[CHANNEL_TYPE];
        const arr = Array.isArray(ct) ? ct : (ct ? [ct] : []);
        if (arr.some(v => (v && (v['@id'] || v)) === channelType)) return node['@id'];
    }
    return null;
}

/** The WebSocketChannel2023 subscription-service URL for a resource, or null. */
export function discoverWebSocketService(resourceUrl) {
    return _discoverChannelService(resourceUrl, WS_CHANNEL);
}

/** The WebhookChannel2023 subscription-service URL for a resource, or null. */
export function discoverWebhookService(resourceUrl) {
    return _discoverChannelService(resourceUrl, WEBHOOK_CHANNEL);
}

/**
 * Subscribe to WebSocket notifications for a resource; returns the `receiveFrom`
 * socket URL, or null if the server has no service or the subscription is refused.
 */
export async function subscribeWebSocket(resourceUrl) {
    const service = await discoverWebSocketService(resourceUrl);
    if (!service) return null;
    try {
        const res = await solidSession.fetch(service, {
            method: 'POST',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': ['https://www.w3.org/ns/solid/notification/v1'],
                // CSS requires the fully-qualified channel-type IRI, not the short name.
                type: WS_CHANNEL,
                topic: resourceUrl,
            }),
        });
        if (!res.ok) return null;
        const body = await res.json();
        return body.receiveFrom || null;
    } catch {
        return null;
    }
}

/**
 * Subscribe `resourceUrl` to the WebhookChannel2023 so the server POSTs a
 * notification to `sendTo` when the resource changes (R77 — closed-app inbox push).
 * Returns true on success, false if the server offers no webhook channel or refuses.
 */
export async function subscribeWebhook(resourceUrl, sendTo) {
    const service = await discoverWebhookService(resourceUrl);
    if (!service || !sendTo) return false;
    try {
        const res = await solidSession.fetch(service, {
            method: 'POST',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': ['https://www.w3.org/ns/solid/notification/v1'],
                type: WEBHOOK_CHANNEL,
                topic: resourceUrl,
                sendTo,
            }),
        });
        return !!(res && res.ok);
    } catch {
        return false;
    }
}

// Default connector: subscribe, then open the native WebSocket. Returns a close()
// function, or null when notifications are unavailable (caller keeps polling).
async function _solidConnect(url, { onOpen, onMessage, onClose, onError }) {
    const receiveFrom = await subscribeWebSocket(url);
    if (!receiveFrom || typeof WebSocket === 'undefined') return null;
    const sock = new WebSocket(receiveFrom);
    sock.onopen = () => onOpen();
    sock.onmessage = () => onMessage();
    sock.onclose = () => onClose();
    sock.onerror = () => onError();
    return () => { try { sock.close(); } catch { /* ignore */ } };
}

/**
 * Watch `url` for changes. Returns an unsubscribe function (idempotent).
 * `connect` is injectable for tests; it defaults to the live Solid subscription.
 */
export function watchResource(url, onChange, { pollMs = 5000, connect = _solidConnect } = {}) {
    let stopped = false;
    let live = false;    // true once the notification socket is open
    let timer = null;
    let close = null;

    const fire = () => { if (!stopped) { try { onChange(); } catch { /* ignore */ } } };
    function poll() {
        if (stopped || live) return;
        fire();
        if (!stopped && !live) timer = setTimeout(poll, pollMs);
    }
    function ensurePolling() { if (!stopped && !live && !timer) timer = setTimeout(poll, pollMs); }
    function stopPolling() { if (timer) { clearTimeout(timer); timer = null; } }

    ensurePolling();   // baseline liveness

    Promise.resolve(
        connect(url, {
            onOpen: () => { if (!stopped) { live = true; stopPolling(); } },
            onMessage: fire,
            onClose: () => { live = false; ensurePolling(); },
            onError: () => { live = false; ensurePolling(); },
        })
    ).then((closer) => {
        if (stopped) { if (typeof closer === 'function') closer(); return; }
        if (typeof closer === 'function') close = closer;
        else ensurePolling();   // no service: stay on polling
    }).catch(() => ensurePolling());

    return function unsubscribe() {
        stopped = true;
        stopPolling();
        if (close) { try { close(); } catch { /* ignore */ } close = null; }
    };
}
