// Invite-link carry-through (Phase A4) — make "click an invite link → get added"
// survive reloads and a fresh first-run install, instead of the old fragile
// fixed-timeout pre-fill (R8.3.1) that raced the connection.
//
// Flow: capturePendingInvite() at startup stashes the inviter address from the
// URL (?from=, or a full invite URL carrying a nested ?from=) into localStorage
// and cleans the URL. consumePendingInvite() runs on the "registered" event —
// i.e. once the socket is provably connected+registered — hands the address to
// the host (which shows the existing deep-link confirm modal) and clears it.
// Idempotent: opening the same link twice / a second registered event is a no-op.
//
// createInvite({ getSocket, onPendingInvite })

const KEY = 'proxion_pending_invite';

export function createInvite({ getSocket, onPendingInvite }) {
    function _readUrlInvite() {
        try {
            const params = new URLSearchParams(window.location.search);
            let from = params.get('from');
            // Accept a full invite URL pasted as ?from=https://gw/invite?from=<addr>
            if (from && /^https?:\/\//i.test(from)) {
                try { from = new URL(from).searchParams.get('from') || from; } catch { /* keep raw */ }
            }
            return from || null;
        } catch { return null; }
    }

    // Call once at startup. Returns the captured address (or null).
    function capturePendingInvite() {
        const from = _readUrlInvite();
        if (!from) return null;
        try { localStorage.setItem(KEY, from); } catch { /* private mode */ }
        try { history.replaceState({}, '', window.location.pathname); } catch { /* noop */ }
        return from;
    }

    // Call on the "registered" event. If an invite is pending and the socket is
    // open, hands it to onPendingInvite() and clears it. Returns the consumed
    // address, or null if nothing to do / not yet connected (will retry on the
    // next registered event).
    function consumePendingInvite() {
        let addr = null;
        try { addr = localStorage.getItem(KEY); } catch { /* private mode */ }
        if (!addr) return null;
        const socket = getSocket();
        if (!socket || socket.readyState !== WebSocket.OPEN) return null;
        try { localStorage.removeItem(KEY); } catch { /* noop */ }
        onPendingInvite(addr);
        return addr;
    }

    return { capturePendingInvite, consumePendingInvite };
}

// What to look up for an Add contact input. Accepts a bare address, any
// http(s) or proxion:// link carrying ?from= (unwrapping one nested invite
// URL), and a gateway's short link https://gw/i/<token>, which carries no
// address: shortLink tells the caller to let the gateway resolve it.
export function contactAddressFromInput(raw) {
    const s = String(raw || '').trim();
    if (/^(https?|proxion):\/\//i.test(s)) {
        try {
            const u = new URL(s);
            let from = u.searchParams.get('from');
            if (from && /^https?:\/\//i.test(from)) {
                try { from = new URL(from).searchParams.get('from') || from; } catch { /* keep */ }
            }
            if (from) return { address: from.trim(), shortLink: false };
            if (/^https?:$/i.test(u.protocol) && /^\/i\/[0-9a-f]{8,64}\/?$/i.test(u.pathname)) {
                return { address: u.origin + u.pathname.replace(/\/$/, ''), shortLink: true };
            }
        } catch { /* not a URL */ }
    }
    return { address: s, shortLink: false };
}

// Whether a Proxion address (did@https://host) names the gateway at *host*,
// i.e. the page's own gateway: an invite from it cannot add anyone.
export function addressIsOnHost(addr, host) {
    const at = String(addr || '').lastIndexOf('@');
    if (at < 0 || !host) return false;
    try { return new URL(addr.slice(at + 1)).host === host; } catch { return false; }
}

// A room code stashed across a sign-in round trip. It only counts while
// recent, so a link opened long ago on an origin the user never set up on is
// not joined when they finally do.
export const PENDING_JOIN_KEY = 'proxion_pending_join';
export const PENDING_JOIN_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export function stashPendingJoin(storage, code, now = Date.now()) {
    try { storage.setItem(PENDING_JOIN_KEY, JSON.stringify({ code, ts: now })); } catch { /* private mode */ }
}

export function readPendingJoin(storage, now = Date.now()) {
    let raw = null;
    try { raw = storage.getItem(PENDING_JOIN_KEY); } catch { return ''; }
    if (!raw) return '';
    try {
        const v = JSON.parse(raw);
        if (v && typeof v.code === 'string' && typeof v.ts === 'number'
                && now - v.ts >= 0 && now - v.ts < PENDING_JOIN_MAX_AGE_MS) return v.code;
    } catch { /* an old undated stash */ }
    try { storage.removeItem(PENDING_JOIN_KEY); } catch { /* ignore */ }
    return '';
}
