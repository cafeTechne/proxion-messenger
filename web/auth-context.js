// Account identity remains bound during expiry; generations invalidate async
// work even when a login restores the same WebID/session fields.
export function createAuthContextTracker(getInfo) {
    let webId = null, epoch = 0, signature = null, previousInfo = null;
    let suppressed = null;
    const rawSignature = info => JSON.stringify([!!info.isLoggedIn, info.webId || null, info.sessionId || null]);
    function current() {
        const info = getInfo() || {}, raw = rawSignature(info);
        if (suppressed && suppressed !== raw) suppressed = null;
        const authenticated = !!info.isLoggedIn && !suppressed;
        if (authenticated && info.webId) webId = info.webId;
        const next = JSON.stringify([webId, authenticated, info.sessionId || null]);
        if (signature !== next || previousInfo !== info) epoch++;
        signature = next; previousInfo = info;
        return Object.freeze({ webId, authenticated, sessionId: info.sessionId || null, epoch });
    }
    function note(event) {
        if (event === 'sessionExtended' && !suppressed) return;
        epoch++;
        if (event === 'logout') webId = null;
        if (event === 'logout' || event === 'sessionExpired') {
            if (event !== 'logout') current(); // latch the account before expiry
            suppressed = rawSignature(getInfo() || {});
        } else suppressed = null;
    }
    return { current, note };
}
