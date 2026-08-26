import solidAuthn from './solid-authn.bundle.js';
import { detectMode } from './transport.js';
const { Session } = solidAuthn;

export const solidSession = new Session({ restorePreviousSession: true });
let _cachedStorageRoot = null;

function _isPrivateIp(url) {
    try {
        const host = new URL(url).hostname;
        return (
            /^127\./.test(host) ||
            /^10\./.test(host) ||
            /^192\.168\./.test(host) ||
            /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
            host === 'localhost' ||
            host === '::1'
        );
    } catch {
        return true;
    }
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
    try {
        await solidSession.logout({ logoutType: 'app' });
    } catch (e) {
        console.warn('OIDC logout failed:', e);
    }
    _cachedStorageRoot = null;
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
    const lsCache = localStorage.getItem('proxion_storage_root_v2');
    if (lsCache) {
        // Reject cached values ending with /proxion/ — old incorrect format.
        if (/^https?:\/\//.test(lsCache) && !lsCache.endsWith('/proxion/')) {
            _cachedStorageRoot = lsCache;
            return _cachedStorageRoot;
        }
        localStorage.removeItem('proxion_storage_root_v2');
    }
    if (!solidSession.info.isLoggedIn) return null;
    const webId = solidSession.info.webId;
    if (!webId || !/^https?:\/\//.test(webId)) return null;
    // Same-origin, account-aware fallback for when the profile omits pim:storage.
    const fromWebId = _rootFromWebId(webId);

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
        localStorage.setItem('proxion_storage_root_v2', _cachedStorageRoot);
        return _cachedStorageRoot;
    } catch {
        clearTimeout(timeout);
        _cachedStorageRoot = fromWebId;
        return fromWebId;
    }
}

export function podStorageRoot() {
    if (_cachedStorageRoot) return _cachedStorageRoot;
    const lsCache = localStorage.getItem('proxion_storage_root_v2');
    if (lsCache && /^https?:\/\//.test(lsCache) && !lsCache.endsWith('/proxion/')) {
        _cachedStorageRoot = lsCache;
        return _cachedStorageRoot;
    }
    if (!solidSession.info.isLoggedIn) return null;
    return _rootFromWebId(solidSession.info.webId);
}
