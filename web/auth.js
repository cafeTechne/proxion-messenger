import solidAuthn from './solid-authn.bundle.js';
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
    await solidSession.login({
        oidcIssuer: issuer,
        redirectUrl: window.location.origin + window.location.pathname,
        clientName: 'Proxion',
    });
}

export async function solidLogout() {
    try {
        await solidSession.logout({ logoutType: 'app' });
    } catch (e) {
        console.warn('OIDC logout failed:', e);
    }
    _cachedStorageRoot = null;
}

export async function discoverStorageRoot() {
    if (_cachedStorageRoot) return _cachedStorageRoot;
    const lsCache = localStorage.getItem('proxion_storage_root');
    if (lsCache) {
        // Reject cached values ending with /proxion/ — old incorrect format.
        if (lsCache.startsWith('https://') && !_isPrivateIp(lsCache) && !lsCache.endsWith('/proxion/')) {
            _cachedStorageRoot = lsCache;
            return _cachedStorageRoot;
        }
        localStorage.removeItem('proxion_storage_root');
    }
    if (!solidSession.info.isLoggedIn) return null;
    const webId = solidSession.info.webId;
    if (!webId || !webId.startsWith('https://')) return null;

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
        if (!storageRoot) storageRoot = new URL(webId).origin + '/';
        if (!storageRoot.startsWith('https://') || _isPrivateIp(storageRoot)) {
            storageRoot = new URL(webId).origin + '/';
        }
        _cachedStorageRoot = storageRoot; // bare root — pod.js owns the proxion/ prefix
        localStorage.setItem('proxion_storage_root', _cachedStorageRoot);
        return _cachedStorageRoot;
    } catch {
        clearTimeout(timeout);
        const fallback = new URL(webId).origin + '/';
        _cachedStorageRoot = fallback;
        return fallback;
    }
}

export function podStorageRoot() {
    if (_cachedStorageRoot) return _cachedStorageRoot;
    const lsCache = localStorage.getItem('proxion_storage_root');
    if (lsCache && lsCache.startsWith('https://') && !_isPrivateIp(lsCache) && !lsCache.endsWith('/proxion/')) {
        _cachedStorageRoot = lsCache;
        return _cachedStorageRoot;
    }
    if (!solidSession.info.isLoggedIn) return null;
    const origin = new URL(solidSession.info.webId).origin;
    return `${origin}/`;
}
