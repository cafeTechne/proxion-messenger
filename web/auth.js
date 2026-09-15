import solidAuthn from './solid-authn.bundle.js';
import { detectMode } from './transport.js';
import { isPrivatePodHost } from './ssrf.js';
import { podQueueClear } from './podqueue.js';
const { Session } = solidAuthn;

export const solidSession = new Session({ restorePreviousSession: true });
let _cachedStorageRoot = null;

const _ROOT_KEY = 'proxion_storage_root_v2';

// Local browser stores that cache decrypted content (DM plaintext, saved
// messages). Their databases are namespaced per authenticated account so one
// account signed in on a shared device can never read another's rows, and they
// are cleared on logout (see accountDbName + solidLogout).
const _DM_HISTORY_DB = 'proxion-dm-history';
const _DM_HISTORY_STORE = 'messages';
const _SAVED_DB = 'proxion-saved-messages';
const _SAVED_STORE = 'saved';

// Reject an untrustworthy pim:storage claim on a private/loopback host (the SSRF
// host check lives in ssrf.js so it stays dependency-free and shared with pod.js).
const _isPrivateIp = isPrivatePodHost;

// Namespace a browser-store database name to the authenticated account, the same
// binding the persisted storage root uses (the current WebID is the account
// identity). This is the primary defense against cross-account bleed: it works
// even without an explicit logout, since a second account signed in on a shared
// origin opens a different-named database and cannot read the first's rows.
// Falls back to the bare base name when no pod account is signed in (gateway-only
// local rooms), preserving the pre-namespacing name for that case.
export function accountDbName(base) {
    const webId = solidSession.info.isLoggedIn && solidSession.info.webId;
    return webId ? `${base}::${webId}` : base;
}

// Clear one object store via its own short-lived connection. IndexedDB allows
// concurrent connections to a database, so this clears the rows even while a
// store module holds its own open connection (unlike deleteDatabase, which the
// open connection would block). Best-effort: resolves on any failure.
function _clearStore(dbName, storeName) {
    return new Promise((resolve) => {
        if (typeof indexedDB === 'undefined') { resolve(); return; }
        let req;
        try { req = indexedDB.open(dbName); } catch { resolve(); return; }
        req.onerror = () => resolve();
        req.onblocked = () => resolve();
        req.onsuccess = (e) => {
            const db = e.target.result;
            try {
                if (!db.objectStoreNames.contains(storeName)) { db.close(); resolve(); return; }
                const tx = db.transaction(storeName, 'readwrite');
                tx.objectStore(storeName).clear();
                tx.oncomplete = () => { db.close(); resolve(); };
                tx.onerror = () => { db.close(); resolve(); };
            } catch { try { db.close(); } catch { /* ignore */ } resolve(); }
        };
    });
}

export async function initSolidAuth() {
    await solidSession.handleIncomingRedirect({
        restorePreviousSession: true,
        url: window.location.href,
    });
    if (window.location.search) {
        history.replaceState(null, '', window.location.pathname);
    }
    return solidSession.info.isLoggedIn ? solidSession.info.webId : null;
}

export async function solidLogin(issuer) {
    const opts = {
        oidcIssuer: issuer,
        redirectUrl: window.location.origin + window.location.pathname,
        clientName: 'Proxion',
    };
    // Web build (R102): present a hosted Solid-OIDC Client Identifier Document so
    // we have a stable client identity and the redirect returns to this static
    // origin. Gateway/desktop keeps dynamic registration via clientName.
    if (detectMode() === 'web') {
        opts.clientId = new URL('clientid.jsonld', window.location.href).href;
    }
    await solidSession.login(opts);
}

export async function solidLogout() {
    // Resolve the account-bound store databases BEFORE the OIDC logout: once the
    // session is torn down the WebID is gone and accountDbName collapses to the
    // bare base name, so the purge below would miss the signed-in account's rows.
    const dmDb = accountDbName(_DM_HISTORY_DB);
    const savedDb = accountDbName(_SAVED_DB);
    try {
        await solidSession.logout({ logoutType: 'app' });
    } catch (e) {
        console.warn('OIDC logout failed:', e);
    }
    _cachedStorageRoot = null;
    // Clear the persisted root too, so the next account signed in on this browser
    // does not inherit the previous account's pod root.
    try { localStorage.removeItem(_ROOT_KEY); } catch { /* ignore */ }
    // Purge the SW cache and the offline send queue so an account switch on a
    // shared device leaks no pod-derived responses or queued message content.
    // Best-effort: never let a failure here block the logout.
    try {
        if (typeof caches !== 'undefined' && caches.keys) {
            const keys = await caches.keys();
            await Promise.all(keys.filter((k) => /^proxion-shell-/.test(k)).map((k) => caches.delete(k)));
        }
    } catch { /* ignore */ }
    try { await podQueueClear(); } catch { /* ignore */ }
    // Belt-and-suspenders over the per-account naming: clear this account's local
    // plaintext stores (decrypted DM history + saved messages) so nothing they
    // cached lingers after logout on a shared device.
    try { await _clearStore(dmDb, _DM_HISTORY_STORE); } catch { /* ignore */ }
    try { await _clearStore(savedDb, _SAVED_STORE); } catch { /* ignore */ }
}

// Is `root` safe to trust as THIS WebID's storage root? Require same origin as the
// WebID, so a stale (other-account) or poisoned (foreign-origin) localStorage value
// cannot redirect authenticated pod writes to somewhere else. A legitimately
// cross-origin pim:storage (e.g. Inrupt PodSpaces) is never persisted (see
// discoverStorageRoot) and is re-derived from the WebID card each session instead.
function _rootTrustedFor(root, webId) {
    if (!root || !/^https?:\/\//.test(root) || root.endsWith('/proxion/')) return false;
    try { return new URL(root).origin === new URL(webId).origin; } catch { return false; }
}

// Read the persisted root only when it was stored for THIS exact WebID, not merely
// the same origin: signing in as a second account on a shared origin (without a
// logout) must not inherit the first account's root. The entry is {webId, root};
// a legacy bare-string value (no bound WebID) is treated as untrusted and dropped.
function _readPersistedRoot(webId) {
    let raw;
    try { raw = localStorage.getItem(_ROOT_KEY); } catch { return null; }
    if (!raw) return null;
    let entry = null;
    try { entry = JSON.parse(raw); } catch { /* legacy bare string */ }
    const root = (entry && typeof entry === 'object') ? entry.root : null;
    if (root && entry.webId === webId && _rootTrustedFor(root, webId)) return root;
    try { localStorage.removeItem(_ROOT_KEY); } catch { /* ignore */ }
    return null;
}

// Persist a same-origin root bound to its WebID (see _readPersistedRoot).
function _persistRoot(root, webId) {
    if (!_rootTrustedFor(root, webId)) return;
    try { localStorage.setItem(_ROOT_KEY, JSON.stringify({ webId, root })); } catch { /* ignore */ }
}

// Derive a pod's storage root from its WebID by stripping the profile document
// path (…/alice/profile/card#me → …/alice/). This mirrors the sender-side
// peerPodRootFromWebId so a recipient's OWN root matches the root a peer derives
// from that same WebID; without this an account-based server (WebID under
// /<account>/) would have the two disagree and every cross-account drop would
// miss. Falls back to the origin for a root-hosted WebID (…/profile/card#me).
function _rootFromWebId(webId) {
    try {
        const noFrag = String(webId).split('#')[0];
        const i = noFrag.indexOf('/profile/');
        if (i > 0) return noFrag.slice(0, i + 1);
        return new URL(webId).origin + '/';
    } catch { return null; }
}

export async function discoverStorageRoot() {
    if (_cachedStorageRoot) return _cachedStorageRoot;
    if (!solidSession.info.isLoggedIn) return null;
    const webId = solidSession.info.webId;
    if (!webId || !/^https?:\/\//.test(webId)) return null;
    // Same-origin, account-aware fallback for when the profile omits pim:storage.
    const fromWebId = _rootFromWebId(webId);
    // Trust the persisted root only if it is bound to THIS exact WebID.
    const cached = _readPersistedRoot(webId);
    if (cached) {
        _cachedStorageRoot = cached;
        return _cachedStorageRoot;
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
        const res = await solidSession.fetch(webId, {
            headers: { Accept: 'text/turtle' },
            signal: controller.signal,
        });
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`WebID fetch failed: ${res.status}`);
        const turtle = await res.text();
        const patterns = [
            /pim:storage\s+<([^>]+)>/,
            /<http:\/\/www\.w3\.org\/ns\/pim\/space#storage>\s+<([^>]+)>/,
            /<([^>]+)>\s+a\s+(?:[^;.]*\s)?(?:pim:Storage|<http:\/\/www\.w3\.org\/ns\/pim\/space#Storage>)/
        ];
        let storageRoot = null;
        for (const pattern of patterns) {
            const match = turtle.match(pattern);
            if (match && match[1]) {
                storageRoot = match[1].replace(/\/?$/, '/');
                break;
            }
        }
        // Trust an https pim:storage claim (it may be cross-origin, as with Inrupt
        // PodSpaces), but reject a non-https or private-IP claim and derive the root
        // from the WebID path instead of collapsing to the bare origin.
        if (storageRoot && (!storageRoot.startsWith('https://') || _isPrivateIp(storageRoot))) {
            storageRoot = null;
        }
        if (!storageRoot) storageRoot = fromWebId;
        _cachedStorageRoot = storageRoot; // bare root — pod.js owns the proxion/ prefix
        // Persist only a same-origin root as the fast-path cache, bound to this
        // WebID; a cross-origin pim:storage stays in memory and is re-derived
        // (authoritatively) next session so a persisted value can never point off
        // the WebID's origin, nor be adopted by a different account.
        _persistRoot(storageRoot, webId);
        return _cachedStorageRoot;
    } catch {
        clearTimeout(timeout);
        _cachedStorageRoot = fromWebId;
        return fromWebId;
    }
}

export function podStorageRoot() {
    if (_cachedStorageRoot) return _cachedStorageRoot;
    if (!solidSession.info.isLoggedIn) return null;
    const webId = solidSession.info.webId;
    const cached = _readPersistedRoot(webId);
    if (cached) {
        _cachedStorageRoot = cached;
        return _cachedStorageRoot;
    }
    // Derive from the WebID (un-poisonable). A cross-origin pim:storage pod fills
    // _cachedStorageRoot via discoverStorageRoot, which onPodLoggedIn awaits before
    // any pod I/O, so the sync fallback here is only used pre-discovery.
    return _rootFromWebId(webId);
}
