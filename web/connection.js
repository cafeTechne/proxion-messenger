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
// })

import { myX25519PubB64u } from './e2e.js';
import { announce } from './a11y.js';
import { t } from './i18n.js';

export function createConnection({
    wsUrl, getSocket, setSocket, getClientDid, generateOrLoadIdentity, handleEventAsync,
}) {
    const state = {
        _reconnectTimer: null,  // "Server unreachable" banner escalation
        _reconnectDelay: 3000,  // exponential backoff; resets to 3000 on successful connect
        _pendingOnConnect: [],  // commands queued while socket is still connecting
        _wasConnected: false,   // for one-shot SR announcements on state change
        _retryTimeout: null,    // the scheduled backoff connect(), cancelled by "Retry now"
        _failures: 0,           // consecutive failed connects; reset on open
    };

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

    // Send payload now if socket is open; otherwise queue it and send on next onopen.
    // If socket is stuck in a closed/backoff state, kicks off a fresh connect immediately.
    function socketSendOrQueue(payload, { statusEl } = {}) {
        const socket = getSocket();
        const rs = socket ? socket.readyState : -1;
        if (rs !== WebSocket.OPEN) console.warn("[Proxion] socketSendOrQueue: socket not open, readyState=", rs, "(0=CONNECTING 1=OPEN 2=CLOSING 3=CLOSED -1=null)");
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify(payload));
            return;
        }
        if (statusEl) { statusEl.textContent = t('conn.connecting'); statusEl.style.color = "var(--text-secondary)"; }
        // If socket is closed (not just still connecting), restart immediately — don't wait
        // for the exponential-backoff timer which may be up to 60s.
        if (!socket || socket.readyState === WebSocket.CLOSED) {
            if (state._reconnectTimer) { clearTimeout(state._reconnectTimer); state._reconnectTimer = null; }
            state._reconnectDelay = 3000;
            connect();
        }
        // After 8s with no connection, nudge the user — but keep waiting (don't hard-fail).
        const nudgeTimer = setTimeout(() => {
            const stillQueued = state._pendingOnConnect.some(p => p.nudgeTimer === nudgeTimer);
            if (stillQueued && statusEl) {
                statusEl.textContent = t('conn.stillConnecting');
            }
        }, 8000);
        // Cap the queue (drop-oldest) so a long outage doesn't accumulate an
        // unbounded flood that all fires at once on reconnect.
        if (state._pendingOnConnect.length >= 200) {
            const dropped = state._pendingOnConnect.shift();
            if (dropped && dropped.nudgeTimer) clearTimeout(dropped.nudgeTimer);
        }
        state._pendingOnConnect.push({ payload, statusEl, nudgeTimer });
    }

    function forceReconnect() {
        const socket = getSocket();
        if (socket && socket.readyState === WebSocket.OPEN) return;
        if (state._reconnectTimer) { clearInterval(state._reconnectTimer); state._reconnectTimer = null; }
        // Cancel the scheduled backoff connect too, or it would fire later and
        // supersede the socket this call opens.
        if (state._retryTimeout) { clearTimeout(state._retryTimeout); state._retryTimeout = null; }
        state._reconnectDelay = 3000;
        const oldSocket = socket;
        setSocket(null); // disown before closing so its onclose is ignored
        if (oldSocket) { try { oldSocket.close(); } catch(e) {} }
        connect();
    }

    function connect() {
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
            const data = JSON.parse(event.data);
            handleEventAsync(data);
        };

        ws.onerror = (err) => {
            console.error("Gateway WebSocket error — check that the gateway is running on", wsUrl, err);
        };

        ws.onclose = () => {
            clearTimeout(_connectTimeout);
            if (getSocket() !== ws) return; // superseded — don't clobber state or schedule reconnect
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
        pending.forEach(({ payload, statusEl, nudgeTimer }) => {
            clearTimeout(nudgeTimer);
            socket.send(JSON.stringify(payload));
            if (statusEl) { statusEl.textContent = ""; }
        });
    }

    return { socketSendOrQueue, forceReconnect, connect, flushPending, state };
}
