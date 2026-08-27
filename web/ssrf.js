// ssrf.js — pure host-safety checks for pod URLs (no dependencies).
//
// A peer's pod root is derived from their WebID, which they control. Before the
// browser makes an authenticated request there (carrying the DPoP-bound token),
// we refuse private / loopback / link-local hosts so a malicious contact cannot
// point us at an internal service. Kept dependency-free so both auth.js and the
// pod I/O layer can use it without an import cycle, and so it is not swept up in
// the test suites that mock ./auth.js.

// True if the URL's host is loopback / private / link-local — i.e. not a real
// public pod. Covers IPv4 private ranges, IPv6 loopback/ULA/link-local, the
// IPv4-mapped IPv6 prefix, and bracketed forms. Exotic numeric IPv4 encodings
// (0x7f.., decimal) are left to the URL parser's own normalization.
export function isPrivatePodHost(url) {
    let host;
    try { host = new URL(url).hostname.toLowerCase(); } catch { return true; }
    if (host.startsWith('[') && host.endsWith(']')) host = host.slice(1, -1);
    if (host === 'localhost' || host.endsWith('.localhost')) return true;
    if (host === '::1' || host === '::') return true;
    if (/^fe80:/.test(host) || /^f[cd][0-9a-f]{2}:/.test(host)) return true;   // fe80::/10, fc00::/7
    if (/^::ffff:/.test(host)) return true;   // IPv4-mapped IPv6 (never a public pod)
    return (
        /^127\./.test(host) ||
        /^10\./.test(host) ||
        /^192\.168\./.test(host) ||
        /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
        /^169\.254\./.test(host) ||
        host === '0.0.0.0'
    );
}

// May we make an authenticated request to this peer pod root, given our own pod
// root? Public https peers (normal cross-pod federation) are always allowed. A
// private host is allowed only when it is our OWN pod origin — which is how a
// local/dev pod (both parties on http://localhost) and a self-hosted single-server
// deployment legitimately work.
export function isPeerPodRootAllowed(peerRoot, selfRoot) {
    if (!peerRoot) return false;
    if (!isPrivatePodHost(peerRoot)) return true;
    try {
        return !!selfRoot && new URL(peerRoot).origin === new URL(selfRoot).origin;
    } catch { return false; }
}
