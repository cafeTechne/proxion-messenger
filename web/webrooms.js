// webrooms.js: shared rooms in the browser build (no gateway).
//
// Two jobs the gateway does for rooms, done here against the pod instead:
//
//   1. Remember the rooms we joined. A joined room lives on its owner's pod, so
//      the joiner keeps a small record of it in their own pod (pod.js
//      podWriteJoinedRooms) with a WebID-scoped localStorage copy as a cache.
//      The helpers below keep that list well-formed.
//   2. Deliver new room messages. The gateway pushes each message to every
//      member; here each room's current Long Chat day file is watched (a Solid
//      notification when the server offers one, polling otherwise) plus a slow
//      backstop poll, and messages not seen before are handed to the app.
//
// Dependencies are injected so this is unit-testable without a browser.

const MAX_JOINED = 200;

/** A joined-room record with only the fields we keep, or null if malformed. */
export function normalizeJoinedRecord(r) {
    if (!r || typeof r !== 'object') return null;
    const str = (v) => (typeof v === 'string' ? v : '');
    const rec = {
        roomId: str(r.roomId),
        owner: str(r.owner),
        ownerPodRoot: str(r.ownerPodRoot),
        container: str(r.container),
        title: str(r.title),
    };
    if (!/^[\w-]{1,128}$/.test(rec.roomId)) return null;
    if (!/^https?:\/\//.test(rec.owner) || !/^https?:\/\//.test(rec.ownerPodRoot)) return null;
    if (!/^https?:\/\//.test(rec.container) || !rec.container.endsWith('/')) return null;
    return rec;
}

/** Normalize a stored list: drop malformed entries and duplicates (last wins). */
export function normalizeJoinedList(list) {
    const byId = new Map();
    for (const r of Array.isArray(list) ? list : []) {
        const rec = normalizeJoinedRecord(r);
        if (rec) { byId.delete(rec.roomId); byId.set(rec.roomId, rec); }
    }
    return [...byId.values()].slice(-MAX_JOINED);
}

/** The list with `rec` added (or replacing the entry for the same room). */
export function withJoinedRecord(list, rec) {
    return normalizeJoinedList([...(list || []), rec]);
}

/** The list without the room `roomId`. */
export function withoutJoinedRecord(list, roomId) {
    return normalizeJoinedList(list).filter((r) => r.roomId !== roomId);
}

/** localStorage key for the joined-room cache, scoped to one account. */
export function joinedCacheKey(webId) {
    return 'proxion_joined_rooms:' + String(webId || '');
}

/** localStorage key for the "join request pending" marker, scoped to one account. */
export function joinPendingKey(webId) {
    return 'proxion_join_pending:' + String(webId || '');
}

/**
 * Watch rooms for new messages.
 *
 *   readDay(room, date)      -> Promise<messages> for one UTC day of the room
 *   dayUrl(room, date)       -> the URL of that day file (what is watched)
 *   watch(url, onChange)     -> unsubscribe()
 *   onNew(roomId, messages, { initial })  new messages, oldest first
 *   pollMs                   backstop poll period
 *
 * A room is { roomId, container, owner }. The first read of a room seeds what
 * has been seen and reports those messages with initial: true, so the app can
 * decide what counts as unread; later reads report only ids not seen before.
 */
export function createRoomSync({
    readDay, dayUrl, watch, onNew,
    pollMs = 15000, now = () => Date.now(),
    setTimer = (fn, ms) => setInterval(fn, ms), clearTimer = (h) => clearInterval(h),
}) {
    const rooms = new Map();   // roomId -> { room, seen:Set, seeded, unwatch, watchedUrl, busy, again }
    let timer = null;

    function _day(offsetDays = 0) { return new Date(now() - offsetDays * 86400000); }

    function _rewatch(entry) {
        const url = dayUrl(entry.room, _day());
        if (url === entry.watchedUrl) return;
        if (entry.unwatch) { try { entry.unwatch(); } catch { /* ignore */ } }
        entry.watchedUrl = url;
        entry.unwatch = null;
        try { entry.unwatch = watch(url, () => { refresh(entry.room.roomId); }); } catch { /* the backstop poll still runs */ }
    }

    async function _read(entry) {
        const d = _day();
        // Just after UTC midnight a message may still land in yesterday's file,
        // and the first read covers yesterday too, so recent history is seeded.
        const early = d.getUTCHours() === 0 && d.getUTCMinutes() < 10;
        const dates = (!entry.seeded || early) ? [_day(1), d] : [d];
        const out = [];
        for (const date of dates) {
            let msgs = [];
            try { msgs = await readDay(entry.room, date); } catch { msgs = []; }
            for (const m of msgs || []) out.push(m);
        }
        return out;
    }

    async function refresh(roomId) {
        const entry = rooms.get(roomId);
        if (!entry) return;
        // One read per room at a time; a change during a read triggers one more.
        if (entry.busy) { entry.again = true; return; }
        entry.busy = true;
        try {
            do {
                entry.again = false;
                _rewatch(entry);
                const msgs = await _read(entry);
                if (rooms.get(roomId) !== entry) return;   // untracked meanwhile
                const fresh = [];
                for (const m of msgs) {
                    if (!m || !m.message_id || entry.seen.has(m.message_id)) continue;
                    entry.seen.add(m.message_id);
                    fresh.push(m);
                }
                const initial = !entry.seeded;
                entry.seeded = true;
                if (fresh.length) {
                    fresh.sort((a, b) => String(a.timestamp || '').localeCompare(String(b.timestamp || '')));
                    try { onNew(roomId, fresh, { initial }); } catch (err) { console.warn('[webrooms] onNew failed:', err); }
                }
            } while (entry.again);
        } finally {
            entry.busy = false;
        }
    }

    function _tick() { for (const id of rooms.keys()) refresh(id); }

    /** Start watching a room (idempotent). Messages we already show can be
     *  passed as `known` so they are never reported. */
    function track(room, { known = [] } = {}) {
        if (!room || !room.roomId || !room.container) return;
        const prev = rooms.get(room.roomId);
        if (prev && prev.room.container === room.container) return;
        if (prev) untrack(room.roomId);
        rooms.set(room.roomId, { room, seen: new Set(known), seeded: false, unwatch: null, watchedUrl: null, busy: false, again: false });
        if (!timer) timer = setTimer(_tick, pollMs);
        refresh(room.roomId);
    }

    /** Mark a message id as seen (our own send), so a read never reports it. */
    function markSeen(roomId, messageId) {
        const entry = rooms.get(roomId);
        if (entry && messageId) entry.seen.add(messageId);
    }

    function untrack(roomId) {
        const entry = rooms.get(roomId);
        if (!entry) return;
        rooms.delete(roomId);
        if (entry.unwatch) { try { entry.unwatch(); } catch { /* ignore */ } }
        if (!rooms.size && timer) { clearTimer(timer); timer = null; }
    }

    function stop() {
        for (const id of [...rooms.keys()]) untrack(id);
    }

    return { track, untrack, refresh, markSeen, stop, tracked: () => [...rooms.keys()] };
}
