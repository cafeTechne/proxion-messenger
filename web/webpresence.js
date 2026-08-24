// webpresence.js — gateway-free presence (R104).
//
// Without a gateway to fan out presence, each client publishes a public-read
// heartbeat to its pod (pod.js podWritePresence) on a timer, and derives a
// contact's status from how fresh their heartbeat is. Coarser than socket
// presence by design: a stale heartbeat becomes "away" then "offline" (last
// seen), rather than a hard real-time signal. Dependencies are injected so the
// engine and the freshness rule are unit-testable without a browser.

// Pure: map a peer's presence doc + current time to a status. A fresh heartbeat
// keeps the doc's own status (online/away/busy); as it ages it decays to away,
// then offline. An explicit "offline" doc is always offline.
export function statusFromHeartbeat(doc, nowMs, { onlineMs = 45000, awayMs = 300000 } = {}) {
    if (!doc || !doc.heartbeat) return { status: 'offline', lastSeen: (doc && doc.heartbeat) || 0 };
    if (doc.status === 'offline') return { status: 'offline', lastSeen: doc.heartbeat };
    const age = nowMs - doc.heartbeat;
    if (age <= onlineMs) return { status: doc.status || 'online', lastSeen: doc.heartbeat };
    if (age <= awayMs) return { status: 'away', lastSeen: doc.heartbeat };
    return { status: 'offline', lastSeen: doc.heartbeat };
}

export function createWebPresence({
    pod, notify, handleEvent, getContacts, peerPodRoot,
    now = () => Date.now(), heartbeatMs = 30000, maxSubs = 100,
}) {
    let _timer = null;
    const _subs = new Map();   // webid -> unsubscribe

    const _hidden = () => (typeof document !== 'undefined' && document.hidden);
    const beat = (status) => pod.podWritePresence(status || (_hidden() ? 'away' : 'online'));

    async function _refreshContact(webid) {
        const doc = await pod.podReadPresence(peerPodRoot(webid));
        const { status, lastSeen } = statusFromHeartbeat(doc, now());
        handleEvent({
            type: 'presence_update', webid, status,
            updated_at: lastSeen ? new Date(lastSeen).toISOString() : null,
        });
    }

    function subscribeContact(webid) {
        if (!webid || _subs.has(webid) || _subs.size >= maxSubs) return;
        _refreshContact(webid).catch(() => {});
        const url = pod.presenceUrlFor(peerPodRoot(webid));
        if (url && notify && notify.watchResource) {
            _subs.set(webid, notify.watchResource(url, () => { _refreshContact(webid).catch(() => {}); }));
        } else {
            _subs.set(webid, () => {});   // reserve the slot even without a live watch
        }
    }

    function syncContacts() {
        for (const w of (getContacts ? getContacts() : [])) subscribeContact(w);
    }

    function start() {
        beat('online');
        _timer = setInterval(() => { beat(); }, heartbeatMs);
        syncContacts();
        if (typeof window !== 'undefined' && window.addEventListener) {
            window.addEventListener('beforeunload', () => { pod.podWritePresence('offline'); });
            document.addEventListener('visibilitychange', () => { beat(); });
        }
    }

    function stop() {
        if (_timer) { clearInterval(_timer); _timer = null; }
        for (const unsub of _subs.values()) { try { unsub(); } catch { /* ignore */ } }
        _subs.clear();
    }

    return { start, stop, beat, syncContacts, subscribeContact, _subs };
}
