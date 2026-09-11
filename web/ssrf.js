// ssrf.js — pure host-safety checks for pod URLs (no dependencies).
//
// A peer's pod root is derived from their WebID, which they control. Before the
// browser makes an authenticated request there (carrying the DPoP-bound token),
// we refuse private / loopback / link-local hosts so a malicious contact cannot
// point us at an internal service. Kept dependency-free so both auth.js and the
// pod I/O layer can use it without an import cycle, and so it is not swept up in
// the test suites that mock ./auth.js.

// True if the URL is not a public pod we may send the DPoP-bound token to: a
// non-https scheme, or a loopback / private / link-local / reserved host. Covers
// the IPv4 private ranges, every IPv6 form that begins with :: (loopback ::1,
// unspecified ::, the IPv4-mapped ::ffff: prefix and the deprecated IPv4-compatible
// ::a.b.c.d form), IPv6 link-local / ULA, bracketed forms, and the numeric IPv4
// encodings — decimal (2130706433), octal (0177.0.0.1), hex (0x7f.1) — which the
// WHATWG URL parser normalizes to dotted-quad for https before we classify them.
//
// This is a LEXICAL check: it cannot see where a DNS name actually resolves, so a
// https://name.example/ whose A record is 127.0.0.1 or 169.254.169.254 still reads
// as public here. That DNS-rebinding gap (a public hostname resolving to a private /
// metadata IP) is an ACCEPTED RISK client-side, not a bug to fix here: the browser
// does not expose name resolution before fetch, so it cannot be closed lexically. It
// is mitigated by the forced cross-origin CORS preflight and the DPoP sender-
// constraint on the token, and the server-side gate (network._resolve_safe_ip) is
// the real IP-level defense for gateway-backed builds. R116 hardened the numeric /
// encoded literal cases below; this lexical pass is defense in depth for the
// gateway-less browser build.
export function isPrivatePodHost(url) {
    let u;
    try { u = new URL(url); } catch { return true; }
    if (u.protocol !== 'https:') return true;   // never carry the token over a non-https scheme
    let host = u.hostname.toLowerCase();
    if (host.startsWith('[') && host.endsWith(']')) host = host.slice(1, -1);
    if (host === 'localhost' || host.endsWith('.localhost')) return true;
    // Any IPv6 that begins with :: is non-public: loopback (::1), unspecified (::),
    // IPv4-mapped (::ffff:7f00:1) and the deprecated IPv4-compatible form (::7f00:1).
    // Global unicast (2000::/3) never starts with ::.
    if (host.startsWith('::')) return true;
    if (/^fe80:/.test(host) || /^f[cd][0-9a-f]{2}:/.test(host)) return true;   // fe80::/10, fc00::/7
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
