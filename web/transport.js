// transport.js — the seam between the two ways Proxion can run.
//
// The desktop app and self-hosted setups talk to a gateway over a WebSocket
// (GatewayTransport, full features). The gateway-less browser build talks
// directly to the user's pod (PodTransport). Both expose the same small
// interface so the rest of the client does not fork on build type:
//
//   mode                     'gateway' | 'web'
//   supports(feature)        boolean — gate UI on this, never on `mode`
//   send(payload)            deliver a command (gateway: WS; web: pod-native)
//   sendDM / publishPresence / subscribePresence / sendSignal / onSignal
//   onIncomingDM(handler)
//   connect() / flushPending() / forceReconnect()
//
// Phase 1 (R102) ships GatewayTransport (a faithful wrapper of the existing
// connection layer, zero behavior change) and a PodTransport stub whose
// realtime operations report unsupported until R103–R105 fill them in.
//
// See docs/WEB_BUILD_ROADMAP.md.

// Feature keys the UI gates on. Pod-only features (rooms/history/invites) work
// in both modes; realtime features are gateway-only until the later phases.
export const FEATURES = ['rooms', 'history', 'invites', 'dm', 'presence', 'calls'];

// Web build capabilities: pod-backed rooms/history/invites (R102), DMs through
// the pod drop box (R103), heartbeat presence (R104), and 1:1 call signaling
// over the pod (R105). The full gateway-free feature set.
//
// Message controls that still speak gateway-only WebSocket commands are NOT in
// this set, so supports() reports them false in the web build: message edit,
// reactions, pinning, delete-for-everyone, and the Settings data/federation
// panels that hit gateway-only HTTP endpoints. The pod transport silently drops
// those commands, so offering the controls would fake success or lose data.
// Wiring these to the pod (podEditChatMessageAt / podSoftDeleteChatMessageAt /
// reaction writes exist for room messages) is a separate future task.
const _WEB_ONLY_SUPPORTED = new Set(['rooms', 'history', 'invites', 'dm', 'presence', 'calls']);

export class NotSupported extends Error {
    constructor(op) {
        super(`Transport operation not supported in this mode: ${op}`);
        this.name = 'NotSupported';
    }
}

// Decide which transport this page should use. Priority:
//   1. Running under Tauri  -> always 'gateway' (the desktop app bundles it).
//   2. <meta name="proxion-mode" content="web|gateway">  (the Pages build ships web).
//   3. ?mode=web|gateway  URL param (dev override; persisted for the session).
//   4. localStorage 'proxion_mode'.
//   5. default 'gateway'.
// Every access is guarded so this is safe to call in the node test environment.
export function detectMode() {
    const _win = typeof window !== 'undefined' ? window : undefined;
    if (_win && _win.__TAURI__) return 'gateway';

    // URL param override, persisted so a reload keeps the chosen mode.
    try {
        if (_win && _win.location && _win.location.search) {
            const p = new URLSearchParams(_win.location.search).get('mode');
            if (p === 'web' || p === 'gateway') {
                try { localStorage.setItem('proxion_mode', p); } catch { /* private mode */ }
                return p;
            }
        }
    } catch { /* no URL */ }

    // Build-time signal: the static Pages build ships this meta tag.
    try {
        if (typeof document !== 'undefined' && document.querySelector) {
            const meta = document.querySelector('meta[name="proxion-mode"]');
            const c = meta && meta.getAttribute('content');
            if (c === 'web' || c === 'gateway') return c;
        }
    } catch { /* no DOM */ }

    try {
        const ls = localStorage.getItem('proxion_mode');
        if (ls === 'web' || ls === 'gateway') return ls;
    } catch { /* no storage */ }

    return 'gateway';
}

// Wrap the existing connection layer (from createConnection). This is a thin,
// faithful adapter: it changes no behavior, it only gives the rest of the app a
// uniform object to consult for mode/supports and to route sends through.
export function createGatewayTransport({ connection }) {
    if (!connection) throw new Error('createGatewayTransport requires a connection');
    return {
        mode: 'gateway',
        supports(_feature) { return true; },
        send(payload) { return connection.socketSendOrQueue(payload); },
        // In gateway mode the realtime paths are ordinary commands over the WS,
        // built by main.js and sent through the same queue.
        sendDM(payload) { return connection.socketSendOrQueue(payload); },
        publishPresence(payload) { return connection.socketSendOrQueue(payload); },
        subscribePresence() { /* gateway pushes presence events; nothing to do */ },
        sendSignal(payload) { return connection.socketSendOrQueue(payload); },
        onSignal() { /* delivered via the gateway event dispatch, not here */ },
        onIncomingDM() { /* delivered via the gateway event dispatch, not here */ },
        connect() { return connection.connect(); },
        flushPending() { return connection.flushPending(); },
        forceReconnect() { return connection.forceReconnect(); },
    };
}

// The gateway-less browser transport. Phase 1 supports only the pod-backed
// features; realtime operations report unsupported and no-op loudly until
// R103 (DMs), R104 (presence), and R105 (calls) implement them.
export function createPodTransport() {
    function _unsupported(op) {
        console.warn(`[Proxion] PodTransport: ${op} is not available in the browser build yet.`);
        throw new NotSupported(op);
    }
    return {
        mode: 'web',
        supports(feature) { return _WEB_ONLY_SUPPORTED.has(feature); },
        send(payload) {
            // Pod-native ops are performed directly via pod.js in web mode; a raw
            // gateway-style send has no destination here.
            console.warn('[Proxion] PodTransport.send: no gateway in web mode', payload && payload.cmd);
        },
        sendDM() { _unsupported('sendDM'); },          // R103
        publishPresence() { _unsupported('publishPresence'); }, // R104
        subscribePresence() { _unsupported('subscribePresence'); }, // R104
        sendSignal() { _unsupported('sendSignal'); },  // R105
        onSignal() { _unsupported('onSignal'); },      // R105
        onIncomingDM() { _unsupported('onIncomingDM'); }, // R103
        connect() { /* no gateway to connect to */ },
        flushPending() { /* nothing queued for a gateway */ },
        forceReconnect() { /* no gateway to reconnect */ },
    };
}

// UI gating (R102.4): the controls that must be hidden when the current
// transport does not support their feature. Rooms/history/invites work in both
// modes, so their controls are never gated; DM and call entry points are hidden
// in the Phase 1 web build (they light up in R103/R105). The web build also
// hides the message controls that speak gateway-only commands the pod transport
// drops (pin panel entry, delete-for-everyone) and the Settings panels that hit
// gateway-only HTTP endpoints (data export/import, federation health).
const _FEATURE_CONTROLS = {
    dm: ['add-peer-btn'],
    calls: ['start-call-btn', 'start-video-call-btn'],
    pins: ['pin-panel-btn'],
    delete: ['delete-for-everyone-btn'],
    'settings-data': ['export-data-btn', 'import-data-label'],
    federation: ['settings-federation-section'],
};

// Message-action buttons are rendered per message (rendering.js) with no stable
// id, and the context-menu entries (ctx-edit/react/pin/delete) have their inline
// display reset each time openCtxMenu runs, so neither can be hidden reliably by
// id. Instead we mark the document root with a class per unsupported feature and
// let style.css hide the matching controls (the CSS rule uses !important to beat
// the inline reset). This keeps the gating centralized here.
const _FEATURE_ROOT_CLASSES = {
    edit: 'gate-no-edit',
    reactions: 'gate-no-reactions',
    pins: 'gate-no-pins',
    delete: 'gate-no-delete',
};

// Pure: the element ids to hide for this transport, given what it supports.
export function gatedControlIds(transport) {
    const ids = [];
    for (const [feature, controls] of Object.entries(_FEATURE_CONTROLS)) {
        if (!transport.supports(feature)) ids.push(...controls);
    }
    return ids;
}

// Pure: the document-root classes that hide the dynamic message controls this
// transport cannot back.
export function gatedRootClasses(transport) {
    const classes = [];
    for (const [feature, cls] of Object.entries(_FEATURE_ROOT_CLASSES)) {
        if (!transport.supports(feature)) classes.push(cls);
    }
    return classes;
}

// Hide the gated controls in the given document (defaults to the live document).
// Returns the ids actually hidden. Also marks the document root with the class
// per unsupported dynamic feature (edit/reactions/pins message buttons), which
// style.css hides. Safe to call in any mode: in gateway mode nothing is gated,
// so it is a no-op.
export function applyTransportGating(transport, doc) {
    const d = doc || (typeof document !== 'undefined' ? document : undefined);
    if (!d || !d.getElementById) return [];
    const hidden = [];
    for (const id of gatedControlIds(transport)) {
        const el = d.getElementById(id);
        if (el) {
            if (el.style) el.style.display = 'none';
            if (el.setAttribute) el.setAttribute('aria-hidden', 'true');
            hidden.push(id);
        }
    }
    const root = d.documentElement;
    if (root && root.classList) {
        for (const cls of gatedRootClasses(transport)) root.classList.add(cls);
    }
    return hidden;
}

// Build the transport for the current page. `connection` is required only for
// gateway mode (the web build does not create one).
export function createTransport({ mode, connection } = {}) {
    const resolved = mode || detectMode();
    if (resolved === 'web') return createPodTransport();
    return createGatewayTransport({ connection });
}
