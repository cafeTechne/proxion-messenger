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
