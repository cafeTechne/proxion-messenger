// connectivity.js — call reachability: build the ICE server list from any credential
// shape a user has, probe what candidates the network can actually gather, and turn that
// into a plain verdict. Pure and dependency-injected so it is trivially testable; the only
// I/O is a throwaway RTCPeerConnection in probeIceCandidates (PC class injectable). See
// PLAN_ROUND_91.

// A short, reputable default STUN set so STUN itself is not a single point of failure.
export const DEFAULT_STUN = [
    'stun:stun.l.google.com:19302',
    'stun:stun1.l.google.com:19302',
];

const _isStun = (u) => typeof u === 'string' && /^stuns?:/i.test(u);
const _isTurn = (u) => typeof u === 'string' && /^turns?:/i.test(u);

/**
 * Normalize any credential shape a user actually has into a valid RTCIceServer[]:
 *   - long-term credential: { url: 'turns:host:port', username, password }
 *   - a pasted provider config:  { raw: [ RTCIceServer, ... ] }
 *   - stun[] is a list of stun/stuns URLs (defaults to DEFAULT_STUN)
 * Bad schemes are dropped. coturn shared-secret (HMAC) creds are time-limited and derived
 * at call time, so those stay in the caller (voice.js _getIceServers), not here.
 */
export function buildIceServers({ stun = DEFAULT_STUN, turn = null } = {}) {
    const servers = [];
    for (const u of stun || []) {
        if (_isStun(u)) servers.push({ urls: u });
    }
    if (turn && Array.isArray(turn.raw)) {
        for (const s of turn.raw) {
            if (!s || (typeof s !== 'object')) continue;
            const urls = Array.isArray(s.urls) ? s.urls : [s.urls];
            if (urls.some((u) => _isTurn(u) || _isStun(u))) servers.push(s);
        }
    } else if (turn && _isTurn(turn.url)) {
        const s = { urls: turn.url };
        if (turn.username) s.username = turn.username;
        const cred = turn.password || turn.credential;
        if (cred) s.credential = cred;
        servers.push(s);
    }
    return servers;
}

/** The ICE candidate type ('host' | 'srflx' | 'relay' | 'prflx'), from the field or SDP. */
export function candidateType(cand) {
    if (!cand) return null;
    if (cand.type) return cand.type;
    const s = typeof cand === 'string' ? cand : (cand.candidate || '');
    const m = /\btyp\s+(host|srflx|relay|prflx)\b/i.exec(s);
    return m ? m[1].toLowerCase() : null;
}

/**
 * Turn gathered candidate-type counts into a plain reachability verdict.
 *   relay > 0 -> 'relay'  : a relay path exists, calls connect even on restrictive networks
 *   srflx > 0 -> 'stun'   : reachable via STUN; most calls connect, symmetric NAT may not
 *   host only -> 'host'   : no path off this machine; calls will likely fail, add a relay
 *   nothing   -> 'none'   : could not gather anything (probe failed / offline)
 * `i18nKey` names the user-facing explanation string.
 */
export function classifyConnectivity(counts = {}) {
    const relay = counts.relay || 0;
    const srflx = counts.srflx || 0;
    const host = counts.host || 0;
    if (relay > 0) return { level: 'relay', i18nKey: 'conn.test.relay' };
    if (srflx > 0) return { level: 'stun', i18nKey: 'conn.test.stun' };
    if (host > 0) return { level: 'host', i18nKey: 'conn.test.host' };
    return { level: 'none', i18nKey: 'conn.test.none' };
}

/**
 * Gather ICE candidates against `iceServers` with a throwaway peer connection (no
 * signaling, no peer) and count them by type. `deps.PC` injects the RTCPeerConnection
 * class for tests; `deps.timeoutMs` bounds the gather. Never throws; resolves to
 * { host, srflx, relay, prflx }.
 */
export async function probeIceCandidates(iceServers, deps = {}) {
    const PC = deps.PC || (typeof RTCPeerConnection !== 'undefined' ? RTCPeerConnection : null);
    const timeoutMs = deps.timeoutMs || 5000;
    const counts = { host: 0, srflx: 0, relay: 0, prflx: 0 };
    if (!PC) return counts;
    let pc;
    try {
        pc = new PC({ iceServers });
        if (pc.createDataChannel) pc.createDataChannel('probe');
        await new Promise((resolve) => {
            let done = false;
            const finish = () => { if (!done) { done = true; resolve(); } };
            const timer = setTimeout(finish, timeoutMs);
            pc.onicecandidate = (e) => {
                if (!e || !e.candidate || !e.candidate.candidate) { clearTimeout(timer); finish(); return; }
                const t = candidateType(e.candidate);
                if (t && counts[t] != null) counts[t] += 1;
            };
            Promise.resolve()
                .then(() => pc.createOffer())
                .then((offer) => pc.setLocalDescription(offer))
                .catch(finish);
        });
    } catch (_) {
        /* return whatever we gathered */
    } finally {
        try { if (pc && pc.close) pc.close(); } catch (_) { /* ignore */ }
    }
    return counts;
}
