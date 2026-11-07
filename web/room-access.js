// Read-only permission checks for joined pod rooms. A denied append is not
// required: an idle member sees removal once access is consistently revoked.
import { isPeerPodRootAllowed } from './ssrf.js';
import { peerPodRootFromWebId } from './webdm.js';

function validRoom(room, session) {
    if (!room || room.owned || !/^[\w-]{1,128}$/.test(room.roomId || '')) return false;
    if (typeof room.owner !== 'string' || room.owner === session.webId) return false;
    const ownerRoot = peerPodRootFromWebId(room.owner);
    if (!ownerRoot || (room.ownerPodRoot && room.ownerPodRoot !== ownerRoot)) return false;
    const container = room.container;
    if (typeof container !== 'string' || !container.endsWith('/') || /[\s?#\\]/.test(container) || container.includes('..')) return false;
    if (!isPeerPodRootAllowed(ownerRoot, session.root) || !isPeerPodRootAllowed(container, session.root)) return false;
    try { return new URL(container).origin === new URL(ownerRoot).origin; } catch { return false; }
}

export function createRoomAccessMonitor({
    fetch, getSession, onRemoved,
    pollMs = 15000, maxRoomsPerPoll = 20, requestTimeoutMs = 10000,
    setTimer = (fn, ms) => setInterval(fn, ms), clearTimer = clearInterval,
}) {
    const rooms = new Map();
    let timer = null, polling = null, cursor = 0, pollGeneration = 0;
    const interval = Math.max(1000, Math.min(300000, Number(pollMs) || 15000));
    const budget = Math.max(1, Math.min(200, Math.trunc(Number(maxRoomsPerPoll) || 20)));
    const requestTimeout = Math.max(1, Math.min(30000, Number(requestTimeoutMs) || 10000));

    function session() {
        try {
            const info = getSession();
            if (!info?.ready || typeof info.webId !== 'string' || typeof info.root !== 'string'
                || !info.root.endsWith('/')) return null;
            return { webId: info.webId, root: info.root };
        } catch { return null; }
    }

    function sameAccount(a, b) { return !!a && !!b && a.webId === b.webId && a.root === b.root; }
    function current(entry) {
        return rooms.get(entry.room.roomId) === entry && sameAccount(entry.account, session());
    }

    function untrack(roomId) {
        rooms.delete(roomId);
        if (!rooms.size) {
            if (timer !== null) { clearTimer(timer); timer = null; }
            pollGeneration++;
            polling = null;
            cursor = 0;
        }
    }

    function track(room) {
        const account = session();
        if (!account || !validRoom(room, account)) return false;
        const old = rooms.get(room.roomId);
        if (old && sameAccount(old.account, account) && old.room.container === room.container && old.room.owner === room.owner) {
            old.room = { ...room };
            return true;
        }
        rooms.set(room.roomId, { room: { ...room }, account, denied: 0, pending: null });
        if (timer === null) timer = setTimer(() => { checkAll().catch(() => {}); }, interval);
        return true;
    }

    function head(url) {
        return new Promise((resolve, reject) => {
            const controller = typeof AbortController === 'function' ? new AbortController() : null;
            let finished = false;
            const finish = (settle, value) => {
                if (finished) return;
                finished = true;
                clearTimeout(deadline);
                settle(value);
            };
            const deadline = setTimeout(() => {
                finish(reject, new TypeError('Room access check timed out'));
                if (controller) controller.abort();
            }, requestTimeout);
            try {
                // Attach both handlers even after timeout. A bridge may not
                // support aborting, and its eventual rejection is still consumed.
                Promise.resolve(fetch(url, { method: 'HEAD', redirect: 'error', cache: 'no-store',
                    headers: { Accept: 'application/json' }, ...(controller ? { signal: controller.signal } : {}) }))
                    .then((response) => finish(resolve, response), (error) => finish(reject, error));
            } catch (error) { finish(reject, error); }
        });
    }
    const denied = (response) => response?.status === 403 && !response.error;

    async function ownSessionWorks(entry) {
        let response = await head(entry.account.root + 'proxion/joined-rooms.json');
        if (!current(entry)) return false;
        // A legacy record is still private and can establish that our session
        // works. Missing or failed canonical reads must not silently count as 200.
        if (response?.status === 404) response = await head(entry.account.root + 'proxion/joined-rooms.jsonld');
        return current(entry) && !!response?.ok && !response.error;
    }

    async function inspect(entry, observedDenial) {
        if (!current(entry)) { entry.denied = 0; return 'stale'; }
        const url = entry.room.container + 'members.jsonld';
        let response;
        try { response = await head(url); } catch { entry.denied = 0; return 'unavailable'; }
        if (!current(entry)) { entry.denied = 0; return 'stale'; }
        if (response?.ok) { entry.denied = 0; return 'allowed'; }
        // 401, missing resources, server failures and bridge policy refusals do
        // not prove removal. They also break a consecutive-denial sequence.
        if (!denied(response)) { entry.denied = 0; return 'unavailable'; }
        try {
            if (!(await ownSessionWorks(entry))) { entry.denied = 0; return 'unavailable'; }
        } catch { entry.denied = 0; return 'unavailable'; }
        if (!current(entry)) { entry.denied = 0; return 'stale'; }
        entry.denied = Math.min(2, entry.denied + (observedDenial ? 2 : 1));
        if (entry.denied < 2) return 'denied';
        // Recheck the peer after proving our own private storage is accessible.
        // A transient denial or session refresh must not remove a valid room.
        try { response = await head(url); } catch { entry.denied = 0; return 'unavailable'; }
        if (!current(entry)) { entry.denied = 0; return 'stale'; }
        if (response?.ok) { entry.denied = 0; return 'allowed'; }
        if (!denied(response)) { entry.denied = 0; return 'unavailable'; }
        let settled;
        try { settled = await onRemoved(entry.room.roomId, { ...entry.room }); }
        catch { return 'denied'; }
        if (settled === false) return 'denied';
        // The callback may have already untracked the room after saving its
        // removal. A replacement room/account must never be removed by this check.
        const replacement = rooms.get(entry.room.roomId);
        if (!sameAccount(entry.account, session()) || (replacement && replacement !== entry)) return 'stale';
        untrack(entry.room.roomId);
        return 'removed';
    }

    function check(roomId, { denied: observedDenial = false } = {}) {
        const entry = rooms.get(roomId);
        if (!entry) return Promise.resolve('stale');
        if (entry.pending) return entry.pending;
        const result = inspect(entry, observedDenial).finally(() => {
            if (entry.pending === result) entry.pending = null;
        });
        entry.pending = result;
        return result;
    }

    function checkAll() {
        if (polling) return polling;
        const ids = [...rooms.keys()];
        if (!ids.length) return Promise.resolve([]);
        const count = Math.min(ids.length, budget);
        const batch = Array.from({ length: count }, (_, i) => ids[(cursor + i) % ids.length]);
        cursor = (cursor + count) % ids.length;
        // Bound work per timer and run sequentially, including mass revocations,
        // so a large room list cannot burst through the gateway's bridge budget.
        const generation = pollGeneration;
        const running = (async () => {
            const results = [];
            for (const id of batch) {
                if (generation !== pollGeneration) break;
                results.push(await check(id));
            }
            return results;
        })().finally(() => { if (polling === running) polling = null; });
        polling = running;
        return running;
    }

    function stop() {
        rooms.clear();
        pollGeneration++;
        polling = null;
        if (timer !== null) { clearTimer(timer); timer = null; }
        cursor = 0;
    }

    return { track, untrack, check, checkAll, stop, tracked: () => [...rooms.keys()] };
}
