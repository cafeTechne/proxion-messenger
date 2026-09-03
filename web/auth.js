import solidAuthn from './solid-authn.bundle.js';
import { detectMode } from './transport.js';
import { isPrivatePodHost } from './ssrf.js';
import { podQueueClear } from './podqueue.js';
const { Session } = solidAuthn;

export const solidSession = new Session({ restorePreviousSession: true });
let _cachedStorageRoot = null;

const _ROOT_KEY = 'proxion_storage_root_v2';

// Reject an untrustworthy pim:storage claim on a private/loopback host (the SSRF
// host check lives in ssrf.js so it stays dependency-free and shared with pod.js).
const _isPrivateIp = isPrivatePodHost;

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
