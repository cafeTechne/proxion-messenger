// callstats.js — derive a call-health level from WebRTC getStats. Pure, no I/O.

/** Classify a connection from round-trip time (ms) and recent packet-loss fraction. */
export function classifyConnection({ rttMs = 0, lossFrac = 0 } = {}) {
    if (rttMs >= 400 || lossFrac >= 0.10) return 'poor';
    if (rttMs >= 200 || lossFrac >= 0.03) return 'fair';
    return 'good';
}

/**
 * Reduce a getStats() report (as an array of stat objects) to { rttMs, packetsLost,
 * packetsReceived, lossFrac }. `prev` is the previous reduction, used to compute loss
 * over the interval (getStats totals are cumulative). Pure.
 */
export function deriveStats(statList, prev) {
    let rttMs = 0;
    let packetsLost = 0;
    let packetsReceived = 0;
    for (const s of statList || []) {
        if (!s) continue;
        if (s.type === 'candidate-pair' && (s.nominated || s.state === 'succeeded')
            && typeof s.currentRoundTripTime === 'number') {
            rttMs = Math.max(rttMs, Math.round(s.currentRoundTripTime * 1000));
        }
        if (s.type === 'inbound-rtp') {
            packetsLost += s.packetsLost || 0;
            packetsReceived += s.packetsReceived || 0;
        }
    }
    let lossFrac = 0;
    if (prev) {
        const dLost = packetsLost - (prev.packetsLost || 0);
        const dRecv = packetsReceived - (prev.packetsReceived || 0);
        const total = dLost + dRecv;
        if (total > 0) lossFrac = Math.max(0, Math.min(1, dLost / total));
    }
    return { rttMs, packetsLost, packetsReceived, lossFrac };
}
