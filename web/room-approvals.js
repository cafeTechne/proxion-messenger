// Account-scoped recovery metadata for retained room requests. Decisions are
// still made by the owner and enforced by the room's authoritative descriptor.
export function roomApprovalStatusKey(webId, root) {
    return 'proxion_room_requests:' + JSON.stringify([webId, root || '']);
}

const fingerprint = msg => JSON.stringify([msg?.kind, msg?.room_id,
    msg?.kind === 'join_request' ? msg?.from_webid : msg?.owner_webid]);
const MAX_ROWS = 200;

export function createRoomApprovalStore({ storage, webId, root }) {
    const key = roomApprovalStatusKey(webId, root), rows = new Map();
    const inbox = root && root.replace(/\/?$/, '/') + 'proxion/join-inbox/';
    function valid(row) {
        return !!webId && !!inbox && row && typeof row.url === 'string' && row.url.startsWith(inbox)
            && /^[\w~-][\w.~-]{0,255}$/.test(row.url.slice(inbox.length))
            && ['join_request', 'join_approved', 'join_declined'].includes(row.kind)
            && /^[\w-]{1,128}$/.test(row.roomId || '')
            && ['pending', 'retry', 'cleanup'].includes(row.state)
            && typeof row.fingerprint === 'string' && row.fingerprint.length <= 4096;
    }
    try {
        const raw = storage?.getItem(key);
        const saved = raw && raw.length <= 512 * 1024 ? JSON.parse(raw) : [];
        if (Array.isArray(saved)) for (const row of saved.slice(-MAX_ROWS)) if (valid(row)) rows.set(row.url, row);
    } catch { /* The pod inbox remains the source for retry when storage is unavailable. */ }
    function save() {
        while (rows.size > MAX_ROWS) rows.delete(rows.keys().next().value);
        try {
            const saved = [...rows.values()].filter(valid);
            if (saved.length || typeof storage?.removeItem !== 'function') storage?.setItem(key, JSON.stringify(saved));
            else storage?.removeItem(key);
        } catch { /* Keep recovery metadata in memory if storage is full. */ }
    }
    return {
        list: () => [...rows.values()],
        get: (url, msg) => {
            const row = rows.get(url);
            return row?.fingerprint === fingerprint(msg) ? row : null;
        },
        put(url, msg, state) {
            rows.set(url, { url, kind: msg?.kind, roomId: msg?.room_id, state, fingerprint: fingerprint(msg) });
            save();
        },
        remove(url) { rows.delete(url); save(); },
    };
}

/** A persistent notice for this account; rendering never interprets inbox HTML. */
export function createRoomApprovalNotice({ element, translate, review = () => {} }) {
    let generation = 0;
    return function show(rows, retry) {
        if (!element) return;
        const current = ++generation;
        const requests = rows.filter(row => row.kind === 'join_request');
        const pending = requests.some(row => row.state === 'pending');
        element.classList.toggle('visible', requests.length > 0);
        const text = element.querySelector('[data-room-request-status]');
        const button = element.querySelector('button');
        if (text) text.textContent = requests.length ? translate(
            pending ? 'join.ownerPending' : requests.every(row => row.state === 'cleanup') ? 'join.ownerCleanup' : 'join.ownerRetry', { count: requests.length }) : '';
        if (!button) return;
        button.disabled = false;
        button.textContent = translate(pending ? 'join.reviewRequests' : 'conn.retryNow', { count: requests.length });
        button.onclick = requests.length && retry ? async () => {
            if (pending) review();
            button.disabled = true;
            try { await retry(); } catch { /* Retained requests remain retryable. */ }
            finally { if (generation === current) button.disabled = false; }
        } : null;
    };
}
