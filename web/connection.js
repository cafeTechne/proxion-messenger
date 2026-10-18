// WebSocket connection lifecycle — connect/reconnect with exponential backoff,
// and socketSendOrQueue (send now or queue until the socket opens). This is the
// first slice of the entangled core: it *reassigns* the host socket, so it takes
// getSocket + setSocket (the getter/setter pair shares main.js's closure
// variable across the module boundary). Reconnect timers + the pending-command
// queue are cluster-owned (state).
//
// createConnection({
//   wsUrl,                  // resolved gateway WS URL (computed once in main.js)
//   getSocket, setSocket,   // reassignable host socket
//   getClientDid,           // identity DID (reassigned only in generateOrLoadIdentity)
//   generateOrLoadIdentity, // idempotent identity bootstrap (stays in main.js)
//   handleEventAsync,       // dispatch entrypoint (stays in main.js)
//   isActive,               // false while another tab is the active one (tablock.js)
// })

import { myX25519PubB64u } from './e2e.js';
import { announce } from './a11y.js';
import { t } from './i18n.js';

// A gateway address typed in Settings, as the ws:// or wss:// URL to connect
// to, or null when it is not one. http(s) becomes ws(s), since people paste
// the address they open in the browser.
export function normalizeGatewayUrl(raw) {
    let s = String(raw || '').trim();
    if (!s) return null;
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = 'ws://' + s;
    let u;
    try { u = new URL(s); } catch { return null; }
    const scheme = { 'ws:': 'ws:', 'wss:': 'wss:', 'http:': 'ws:', 'https:': 'wss:' }[u.protocol];
    if (!scheme || u.username || u.password) return null;
    // Chrome percent-encodes junk like spaces into a ws:// host instead of
    // throwing, so check the host is a name or an IP address.
    if (!/^(\[[0-9a-f:.]+\]|[a-z0-9-]+(\.[a-z0-9-]+)*)$/i.test(u.hostname)) return null;
    u.protocol = scheme;
    const out = u.toString();
    return u.pathname === '/' && !u.search && !u.hash ? out.replace(/\/$/, '') : out;
}

// The room / DM list a connect delivers (the register snapshot and the
// get_rooms reply both arrive) as what to do per thread. The gateway sends
// unread_count with each entry, so threads in the background just get their
// badge set (not added to: the list arrives twice). Only the open thread, once
// per connection, gets its history refetched to pick up missed messages.
// Fetching every thread's history here used to exceed the gateway's
// per-connection command budget with a few dozen rooms, which emptied the
// sidebars. `fetched` is a Set the caller resets on each new connection.
export function planThreadCatchUp(entries, { activeId = null, muted = new Set(), fetched = new Set() } = {}) {
    const counts = {};
    const fetch = [];
    for (const e of entries || []) {
        if (!e || !e.id) continue;
        if (e.id === activeId) {
            if (!fetched.has(e.id)) { fetched.add(e.id); fetch.push(e.id); }
            continue;
        }
        if (typeof e.unread_count === 'number' && !muted.has(e.id)) counts[e.id] = e.unread_count;
    }
    return { counts, fetch };
}

// A thread's badge after a catch-up that counted `counted` unread messages
// (a history fetch or the contact list's unread_count). The count is
// absolute, not new since last time, and the same catch-up can arrive more
// than once per connect, so it never adds: the badge keeps the larger of
// the two (a live message counted meanwhile is in the catch-up too).
export function catchUpUnread(current, counted) {
    const a = Number(current) || 0;
    const b = Number(counted) || 0;
    return Math.max(a, b);
}

// Reads the client resends on its own when the gateway refuses them with
// E_RATE (the refusal names the command and says when to retry), so a burst
// never leaves the sidebars silently empty. payloadFor(cmd) rebuilds the
// command (null to drop it). After maxAttempts in one connection the error is
// reported as usual. reset() on each new connection.
export const RATE_RETRY_CMDS = new Set([
    'get_rooms', 'get_dms', 'get_identity', 'list_friend_requests', 'get_relationships',
    'list_blocks', 'get_local_history', 'get_room_members', 'pod_status',
]);

export function createRateRetry({ send, payloadFor, schedule = setTimeout, maxAttempts = 3 }) {
    const attempts = new Map();
    return {
        // True when the error was a retryable rate limit and a retry is scheduled.
        handle(event) {
            if (!event || event.code !== 'E_RATE' || !RATE_RETRY_CMDS.has(event.cmd)) return false;
            const n = (attempts.get(event.cmd) || 0) + 1;
            if (n > maxAttempts) return false;
            attempts.set(event.cmd, n);
            const secs = Math.min(10, Math.max(1, Number(event.retry_after) || 2));
            schedule(() => {
                const p = payloadFor(event.cmd);
                if (p) send(p);
            }, secs * 1000);
            return true;
        },
        reset() { attempts.clear(); },
    };
}

// Commands the gateway does not count against its general budget of 60 per
// 10 s per socket: pings, file chunks, call signaling and the snapshot reads
// each have their own (gateway.py process_command; a Python test keeps these
// lists in step with the gateway's). Everything else is paced.
export const UNPACED_CMDS = new Set([
    'ping', 'pong', 'file_chunk',
    'ice_candidate', 'voice_invite', 'voice_answer', 'voice_hangup',
    // command_validation.SNAPSHOT_READ_COMMANDS
    'get_rooms', 'get_dms', 'get_local_history', 'get_room_members', 'get_message',
    'get_pins', 'get_receipts', 'get_message_readers', 'get_room_bans',
    'get_room_roles', 'list_room_emoji', 'get_disappear_timer', 'list_scheduled',
    'get_notifications', 'get_identity', 'get_connect_id', 'get_my_address',
    'get_gateway_webid', 'get_inbox_webhook', 'get_relationships',
    'list_friend_requests', 'list_blocks', 'get_contact_verification',
    'list_verified_contacts', 'get_presence', 'get_all_presence',
    'get_voice_channel_state', 'tunnel_status', 'pod_status', 'list_sessions', 'get_account_sync',
    'list_webhooks', 'list_devices', 'get_peer_devices', 'get_peer_device_keys',
    'get_sender_key', 'list_dm_sessions', 'update_last_read',
]);
// Paced frames allowed in any 10 s window: under the gateway's 60, with room
// for clock skew between the two windows.
export const PACE_LIMIT = 54;
export const PACE_WINDOW_MS = 10000;

function _cmdOf(data) {
    const m = typeof data === 'string' ? /"cmd"\s*:\s*"([^"\\]+)"/.exec(data) : null;
    return m ? m[1] : '';
}

// Wraps one socket's send so a burst (fast typing, a pasted list, many edits)
// is spread out instead of refused with E_RATE: paced frames beyond the window
// wait, in order, until it has room. isOpen() stops a drain into a socket that
// has since closed; takeHeld() hands the waiting frames back.
export function createSendPacer({
    send, isOpen = () => true, limit = PACE_LIMIT, windowMs = PACE_WINDOW_MS,
    now = () => Date.now(), schedule = setTimeout, cancel = clearTimeout,
}) {
    const sentAt = [];
    const held = [];
    let timer = null;
    function _prune(ts) { while (sentAt.length && ts - sentAt[0] >= windowMs) sentAt.shift(); }
    function _arm(ts) { if (!timer && held.length) timer = schedule(_drain, Math.max(0, sentAt[0] + windowMs - ts)); }
    function _drain() {
        timer = null;
        if (!isOpen()) return;
        const ts = now();
        _prune(ts);
        while (held.length && sentAt.length < limit) { sentAt.push(ts); send(held.shift()); }
        _arm(ts);
    }
    function push(data) {
        if (UNPACED_CMDS.has(_cmdOf(data))) { send(data); return; }
        const ts = now();
        _prune(ts);
        if (!held.length && sentAt.length < limit) { sentAt.push(ts); send(data); return; }
        held.push(data);
        _arm(ts);
    }
    function takeHeld() {
        if (timer) { cancel(timer); timer = null; }
        return held.splice(0);
    }
    return { push, takeHeld };
}

// Commands worth keeping across a closed tab when they could not be sent: the
// user's own writes. Reads, typing and presence are only useful live.
export const PERSIST_CMDS = new Set([
    'send_room', 'send_dm', 'local_dm', 'send_dm_fanout',
    'edit_message', 'edit_local_message', 'delete_local_message',
    'pin_message', 'unpin_message', 'add_reaction', 'remove_reaction',
]);
// Never carried over from a dropped socket: they belong to that connection.
const _SOCKET_ONLY_CMDS = new Set(['register', 'auth_response', 'ping', 'pong', 'typing']);

// The identity of a queued command, for the durable queue and for dedup.
export function queueKey(payload) { return JSON.stringify(payload); }

// Heartbeat: ping an open socket this often; no frame back within the timeout
// means it is half-open (sleep, network change, a proxy dropped it) and is
// replaced.
const HEARTBEAT_MS = 25000;
const PONG_TIMEOUT_MS = 10000;
const PROBE_TIMEOUT_MS = 5000;

export function createConnection({
    wsUrl, getSocket, setSocket, getClientDid, generateOrLoadIdentity, handleEventAsync,
    isActive = () => true,
    persist = null,         // durable queue store (sendqueue.js): save/remove/load
}) {
    const state = {
        _reconnectTimer: null,  // "Server unreachable" banner escalation
        _reconnectDelay: 3000,  // exponential backoff; resets to 3000 on successful connect
        _pendingOnConnect: [],  // commands queued while socket is still connecting
        _wasConnected: false,   // for one-shot SR announcements on state change
        _retryTimeout: null,    // the scheduled backoff connect(), cancelled by "Retry now"
        _failures: 0,           // consecutive failed connects; reset on open
        _stopped: false,        // set by shutdown(): this tab handed over to another
        _heartbeat: null,       // ping interval for the open socket
        _pongTimer: null,       // armed by a ping; any inbound frame clears it
        _restored: false,       // the durable queue was replayed this page load
    };
    const _pacers = new WeakMap();    // socket -> its send pacer
    const _sentKeys = new Set();      // queueKey of persisted-kind frames sent this page load

    // After this many failed attempts in a row the banner also asks whether the
    // app is running at all (the usual cause on a desktop install).
    const HINT_AFTER_FAILURES = 3;

    function _bannerText(el, text) {
        const span = document.getElementById("conn-banner-text");
        if (span) span.textContent = text; else if (el) el.textContent = text;
    }
    function _offlineText(secs) {
        const base = t('conn.unreachable', { secs });
        return state._failures >= HINT_AFTER_FAILURES ? `${base} ${t('conn.isAppRunning')}` : base;
    }
    // The banner's "Retry now" button. Wired lazily on the first connect so the
    // module stays importable without a DOM.
    let _retryWired = false;
    function _wireRetry() {
        if (_retryWired) return;
        const btn = document.getElementById("conn-retry-btn");
        if (!btn || typeof btn.addEventListener !== "function") return;
        _retryWired = true;
        btn.addEventListener("click", () => {
            _bannerText(document.getElementById("conn-banner"), t('conn.connectingShort'));
            forceReconnect();
        });
    }

    function _isPersisted(payload) { return !!(persist && payload && PERSIST_CMDS.has(payload.cmd)); }

    // Queue a command for the next registered connection. The user's writes
    // also go to the durable queue so closing the tab does not lose them.
    function _enqueue(payload, statusEl = null, nudgeTimer = null) {
        let key = null;
        if (_isPersisted(payload)) {
            key = queueKey(payload);
            Promise.resolve().then(() => persist.save({ key, owner: getClientDid(), payload, queued_at: Date.now() }))
                .catch(() => { /* best-effort: the in-memory queue still has it */ });
        }
        // Cap the queue (drop-oldest) so a long outage doesn't accumulate an
        // unbounded flood that all fires at once on reconnect.
        if (state._pendingOnConnect.length >= 200) {
            const dropped = state._pendingOnConnect.shift();
            if (dropped && dropped.nudgeTimer) clearTimeout(dropped.nudgeTimer);
            if (dropped && dropped.key) _forget(dropped.key);
        }
        state._pendingOnConnect.push({ payload, statusEl, nudgeTimer, key });
    }

    function _forget(key) {
        if (!persist || !key) return;
        Promise.resolve().then(() => persist.remove(key)).catch(() => {});
    }

    function _sendNow(socket, payload) {
        socket.send(JSON.stringify(payload));
        if (_isPersisted(payload)) _sentKeys.add(queueKey(payload));
    }

    // Send payload now if socket is open; otherwise queue it and send on next onopen.
    // If socket is stuck in a closed/backoff state, kicks off a fresh connect immediately.
    function socketSendOrQueue(payload, { statusEl } = {}) {
        const socket = getSocket();
        if (socket && socket.readyState === WebSocket.OPEN) {
            _sendNow(socket, payload);
            return;
        }
        if (statusEl) { statusEl.textContent = t('conn.connecting'); statusEl.style.color = "var(--text-secondary)"; }
        // If socket is closed (not just still connecting), restart immediately: don't wait
        // for the exponential-backoff timer which may be up to 60s. connect() also
        // cancels that timer, so it cannot open a second socket later.
        if (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
            state._reconnectDelay = 3000;
            if (socket && socket.readyState === WebSocket.CLOSING) setSocket(null);
            connect();
        }
        // After 8s with no connection, nudge the user, but keep waiting (don't hard-fail).
        const nudgeTimer = setTimeout(() => {
            const stillQueued = state._pendingOnConnect.some(p => p.nudgeTimer === nudgeTimer);
            if (stillQueued && statusEl) {
                statusEl.textContent = t('conn.stillConnecting');
            }
        }, 8000);
        _enqueue(payload, statusEl, nudgeTimer);
    }

    // Send a command another tab handed over or a closed tab left behind,
    // unless this tab already sent or queued the identical frame.
    function sendOnce(payload) {
        const key = queueKey(payload);
        if (_sentKeys.has(key) || state._pendingOnConnect.some(p => queueKey(p.payload) === key)) {
            if (_sentKeys.has(key)) _forget(key);
            return;
        }
        socketSendOrQueue(payload);
        if (_sentKeys.has(key)) _forget(key);
    }

    // Replay the durable queue once per page load, after register. Only this
    // identity's rows: another account on this browser keeps its own.
    async function restorePersisted() {
        if (!persist || state._restored) return;
        state._restored = true;
        let rows = [];
        try { rows = (await persist.load()) || []; } catch (_) { state._restored = false; return; }
        const me = getClientDid();
        rows.filter(r => r && r.payload && r.owner === me)
            .sort((a, b) => (a.queued_at || 0) - (b.queued_at || 0))
            .forEach(r => sendOnce(r.payload));
    }

    // Frames a pacer was still holding when its socket went away: back into
    // the queue (ahead of anything queued since) for the next connection.
    function _requeueHeld(ws) {
        const pacer = _pacers.get(ws);
        if (!pacer) return;
        const frames = pacer.takeHeld();
        if (!frames.length) return;
        const later = state._pendingOnConnect.splice(0);
        for (const f of frames) {
            let p;
            try { p = JSON.parse(f); } catch (_) { continue; }
            if (!p || _SOCKET_ONLY_CMDS.has(p.cmd)) continue;
            _enqueue(p);
        }
        state._pendingOnConnect.push(...later);
    }

    function _stopHeartbeat() {
        if (state._heartbeat) { clearInterval(state._heartbeat); state._heartbeat = null; }
        if (state._pongTimer) { clearTimeout(state._pongTimer); state._pongTimer = null; }
    }

    function _ping(ws, timeoutMs) {
        if (ws.readyState !== WebSocket.OPEN) return;
        try { ws.send(JSON.stringify({ cmd: 'ping' })); } catch (_) { /* the timer below catches it */ }
        if (state._pongTimer) clearTimeout(state._pongTimer);
        state._pongTimer = setTimeout(() => {
            state._pongTimer = null;
            if (getSocket() !== ws) return;
            console.warn("[Proxion] No reply from the gateway; reconnecting");
            _restart();
        }, timeoutMs);
    }

    // Check the connection now: an open socket must answer a ping within
    // timeoutMs, a closed one reconnects at once instead of waiting out the
    // backoff. Used on network change, tab focus and a message Retry.
    function probe(timeoutMs = PROBE_TIMEOUT_MS) {
        if (state._stopped || !isActive()) return;
        const s = getSocket();
        if (!s || s.readyState === WebSocket.CLOSED || s.readyState === WebSocket.CLOSING) { _restart(); return; }
        if (s.readyState === WebSocket.OPEN) _ping(s, timeoutMs);
    }

    let _lifecycleWired = false;
    function _wireLifecycle() {
        if (_lifecycleWired) return;
        _lifecycleWired = true;
        if (typeof window !== 'undefined' && window && typeof window.addEventListener === 'function') {
            window.addEventListener('online', () => probe());
            window.addEventListener('offline', () => probe());
        }
        if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') probe();
            });
        }
    }

    // Drop the current socket, whatever its state, and connect fresh.
    function _restart() {
        if (state._reconnectTimer) { clearInterval(state._reconnectTimer); state._reconnectTimer = null; }
        if (state._retryTimeout) { clearTimeout(state._retryTimeout); state._retryTimeout = null; }
        _stopHeartbeat();
        state._reconnectDelay = 3000;
        const oldSocket = getSocket();
        setSocket(null); // disown before closing so its onclose is ignored
        if (oldSocket) {
            _requeueHeld(oldSocket);
            try { oldSocket.close(); } catch(e) {}
            const dot = document.querySelector(".dot");
            if (dot) dot.className = "dot offline";
        }
        connect();
    }

    function forceReconnect() {
        const socket = getSocket();
        if (socket && socket.readyState === WebSocket.OPEN) return;
        _restart();
    }

    function connect() {
        // Only the active tab talks to the gateway (tablock.js): a second socket
        // for the same account would fork the DM ratchet.
        if (state._stopped || !isActive()) return;
        // Single flight: one socket at a time. A connect while one is opening
        // or open (a send during the backoff, "Retry now", a network event) is
        // a no-op, and a scheduled backoff attempt is cancelled.
        const current = getSocket();
        if (current && (current.readyState === WebSocket.CONNECTING || current.readyState === WebSocket.OPEN)) return;
        if (state._retryTimeout) { clearTimeout(state._retryTimeout); state._retryTimeout = null; }
        if (state._reconnectTimer) { clearInterval(state._reconnectTimer); state._reconnectTimer = null; }
        _wireLifecycle();
        // Each call captures its own ws reference so stale onclose/onopen events
        // from a superseded socket cannot overwrite state or schedule extra reconnects.
        _wireRetry();
        // First attempt: replace the static index.html placeholder with the
        // localized label.
        if (!state._wasConnected && state._failures === 0) {
            const nameEl = document.getElementById("username");
            if (nameEl) nameEl.innerText = t('conn.connectingShort');
        }
        const ws = new WebSocket(wsUrl);
        if (typeof ws.send === 'function') {
            const rawSend = ws.send.bind(ws);
            const pacer = createSendPacer({ send: rawSend, isOpen: () => ws.readyState === WebSocket.OPEN });
            _pacers.set(ws, pacer);
            ws.send = (data) => pacer.push(data);
        }
        setSocket(ws);

        // If the port is silently filtered (Windows Firewall etc.) the socket hangs
        // in CONNECTING forever. Force-close after 8s so the error path runs.
        const _connectTimeout = setTimeout(() => {
            if (ws.readyState === WebSocket.CONNECTING) {
                console.warn("[Proxion] Connect timeout — gateway unreachable at", wsUrl);
                ws.close();
            }
        }, 8000);

        ws.onopen = async () => {
            if (getSocket() !== ws) { ws.close(); return; } // superseded
            clearTimeout(_connectTimeout);
            // Ensure identity is always ready before we try to register.
            // generateOrLoadIdentity() is idempotent — if already loaded it returns instantly.
            await generateOrLoadIdentity();
            if (getSocket() !== ws) return; // socket superseded while we were loading identity
            console.log("Connected to gateway");
            // Announce a reconnect (once) to screen readers — but not the very
            // first connect (that's the expected startup state, not news).
            if (state._wasConnected) announce(t('conn.reconnected'));
            state._wasConnected = true;
            state._reconnectDelay = 3000;
            state._failures = 0;
            document.querySelector(".dot").className = "dot online";
            const _connName = localStorage.getItem("proxion_display_name");
            document.getElementById("username").innerText = _connName || t('conn.online');
            document.getElementById("conn-banner").style.display = "none";
            if (state._reconnectTimer) { clearTimeout(state._reconnectTimer); state._reconnectTimer = null; }
            _stopHeartbeat();
            state._heartbeat = setInterval(() => {
                if (getSocket() !== ws) { clearInterval(state._heartbeat); state._heartbeat = null; return; }
                _ping(ws, PONG_TIMEOUT_MS);
            }, HEARTBEAT_MS);
            // NOTE: queued commands are NOT flushed here — they must wait until we
            // are actually REGISTERED (and, under require_auth, past the challenge).
            // Flushing at onopen sent them before register, so the gateway dropped
            // them as "Not registered" and offline-composed messages were lost.
            // flushPending() is called from the "registered" event handler instead.
            // Register with this client's own DID (always — every user has one)
            // Include x25519_pub so peers learn our E2E key when we reconnect
            // Include display_name so the gateway has it immediately (avoids a separate set_identity before auth)
            const _regPayload = {cmd: "register", did: getClientDid()};
            const _storedName = localStorage.getItem("proxion_display_name");
            if (_storedName) _regPayload.display_name = _storedName;
            const _e2ePub = myX25519PubB64u();
            if (_e2ePub) _regPayload.x25519_pub = _e2ePub;
            // Multi-device: if this device was linked to an account, attach the
            // delegation cert so the gateway admits it AS the account (the DID we
            // register/sign with is still this device's own clientDid).
            const _delegCert = localStorage.getItem("proxion_delegation_cert");
            if (_delegCert) {
                try { _regPayload.delegation_cert = JSON.parse(_delegCert); } catch (_) { /* corrupt — ignore */ }
            }
            ws.send(JSON.stringify(_regPayload)); // clientDid always set after generateOrLoadIdentity()
            // All other init commands are deferred to the "registered" event handler so
            // they never race with the auth challenge-response cycle under require_auth mode.
            // Welcome-screen feedback: REPLACE the static "Connect to the gateway…"
            // hint instead of appending below it (the two lines contradicted each
            // other), and never inject into an open conversation — the old
            // unconditional append stacked a duplicate "Connected to gateway." into
            // whatever thread was on screen after every reconnect.
            const _feed = document.getElementById("message-feed");
            const _hint = Array.from(_feed?.querySelectorAll?.(".system-msg") || [])
                .find(el => /connect to the gateway/i.test(el.textContent));
            if (_hint) _hint.textContent = t('conn.connectedHint');
        };

        ws.onmessage = (event) => {
            if (getSocket() !== ws) return; // superseded
            // Any frame proves the socket is alive.
            if (state._pongTimer) { clearTimeout(state._pongTimer); state._pongTimer = null; }
            let data;
            try { data = JSON.parse(event.data); } catch (_) { console.warn("[Proxion] Ignoring a frame that is not JSON"); return; }
            if (!data || data.type === 'pong') return;
            // A gateway from before the ping command answers with this error.
            if (data.type === 'error' && data.message === 'Unknown command: ping') return;
            handleEventAsync(data);
        };

        ws.onerror = (err) => {
            console.error("Gateway WebSocket error — check that the gateway is running on", wsUrl, err);
        };

        ws.onclose = () => {
            clearTimeout(_connectTimeout);
            if (getSocket() !== ws) return; // superseded — don't clobber state or schedule reconnect
            _stopHeartbeat();
            _requeueHeld(ws);
            console.log("Disconnected from gateway");
            // Announce the drop once (the per-second countdown stays silent — it
            // would otherwise announce every second).
            if (state._wasConnected) announce(t('conn.lost'), true);
            state._wasConnected = false;
            state._failures++;
            document.querySelector(".dot").className = "dot offline";
            const banner = document.getElementById("conn-banner");
            // First attempt: retry immediately. Subsequent attempts: exponential backoff.
            const retryMs = state._reconnectDelay === 3000 ? 0 : state._reconnectDelay;
            state._reconnectDelay = Math.min(state._reconnectDelay * 2, 30000);
            if (retryMs === 0) {
                // Instant retry — don't flash "Offline" for a transient hiccup
                document.getElementById("username").innerText = t('conn.connectingShort');
                banner.style.display = "none";
                setTimeout(connect, 0);
            } else {
                document.getElementById("username").innerText = t('conn.offline');
                _bannerText(banner, _offlineText(Math.round(retryMs / 1000)));
                banner.style.display = "flex";
                let remaining = Math.round(retryMs / 1000);
                state._reconnectTimer = setInterval(() => {
                    remaining--;
                    if (remaining > 0) {
                        _bannerText(banner, _offlineText(remaining));
                    } else {
                        clearInterval(state._reconnectTimer);
                        state._reconnectTimer = null;
                    }
                }, 1000);
                state._retryTimeout = setTimeout(() => {
                    state._retryTimeout = null;
                    if (state._reconnectTimer) { clearInterval(state._reconnectTimer); state._reconnectTimer = null; }
                    connect();
                }, retryMs);
            }
        };
    }

    // Flush commands queued while offline/connecting. Called once we're provably
    // REGISTERED (from the "registered" event), so they land after auth — not
    // before it, where the gateway would drop them as "Not registered".
    function flushPending() {
        const socket = getSocket();
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        const pending = state._pendingOnConnect.splice(0);
        pending.forEach(({ payload, statusEl, nudgeTimer, key }) => {
            clearTimeout(nudgeTimer);
            _sendNow(socket, payload);
            _forget(key);
            if (statusEl) { statusEl.textContent = ""; }
        });
    }

    // Another tab is taking over: stop for good and return the commands that
    // never reached the gateway so the new tab can send them. The socket is
    // disowned before it closes so its onclose schedules no reconnect.
    function shutdown() {
        state._stopped = true;
        _stopHeartbeat();
        if (state._reconnectTimer) { clearInterval(state._reconnectTimer); state._reconnectTimer = null; }
        if (state._retryTimeout) { clearTimeout(state._retryTimeout); state._retryTimeout = null; }
        const socket = getSocket();
        setSocket(null);
        if (socket) { _requeueHeld(socket); try { socket.close(); } catch (_) { /* already closed */ } }
        const pending = state._pendingOnConnect.splice(0);
        pending.forEach(({ nudgeTimer }) => clearTimeout(nudgeTimer));
        return pending.map(({ payload }) => payload);
    }

    return { socketSendOrQueue, sendOnce, restorePersisted, probe, forceReconnect, connect, flushPending, shutdown, state };
}
