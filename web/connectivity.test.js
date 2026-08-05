import { describe, it, expect } from 'vitest';
import {
    buildIceServers, candidateType, classifyConnectivity, probeIceCandidates, DEFAULT_STUN,
} from './connectivity.js';

describe('buildIceServers', () => {
    it('defaults to the reputable STUN set', () => {
        const s = buildIceServers();
        expect(s.map((x) => x.urls)).toEqual(DEFAULT_STUN);
    });

    it('normalizes a long-term credential TURN server', () => {
        const s = buildIceServers({ stun: [], turn: { url: 'turns:relay.example:5349', username: 'u', password: 'p' } });
        expect(s).toEqual([{ urls: 'turns:relay.example:5349', username: 'u', credential: 'p' }]);
    });

    it('accepts credential as an alias for password', () => {
        const s = buildIceServers({ stun: [], turn: { url: 'turn:r:3478', username: 'u', credential: 'c' } });
        expect(s[0].credential).toBe('c');
    });

    it('accepts a pasted provider config (raw RTCIceServer[]), keeping turn/stun entries', () => {
        const raw = [
            { urls: 'stun:s.example:3478' },
            { urls: ['turn:t.example:3478', 'turns:t.example:5349'], username: 'u', credential: 'c' },
            { urls: 'https://not-a-relay' },   // dropped
        ];
        const s = buildIceServers({ stun: [], turn: { raw } });
        expect(s).toHaveLength(2);
        expect(s[1].username).toBe('u');
    });

    it('drops a TURN url with a bad scheme', () => {
        expect(buildIceServers({ stun: [], turn: { url: 'http://relay', username: 'u', password: 'p' } })).toEqual([]);
    });

    it('drops non-stun entries from the stun list', () => {
        expect(buildIceServers({ stun: ['stun:ok:3478', 'ftp:bad'] })).toEqual([{ urls: 'stun:ok:3478' }]);
    });
});

describe('candidateType', () => {
    it('prefers the explicit type field', () => {
        expect(candidateType({ type: 'relay' })).toBe('relay');
    });
    it('parses the type out of the raw candidate string', () => {
        expect(candidateType({ candidate: 'candidate:1 1 udp 2130706431 1.2.3.4 5000 typ srflx raddr 0.0.0.0' })).toBe('srflx');
        expect(candidateType('candidate:9 1 udp 1 10.0.0.1 4000 typ host')).toBe('host');
    });
    it('is null for nothing recognizable', () => {
        expect(candidateType(null)).toBeNull();
        expect(candidateType({ candidate: 'garbage' })).toBeNull();
    });
});

describe('classifyConnectivity', () => {
    it('relay beats stun beats host', () => {
        expect(classifyConnectivity({ host: 3, srflx: 2, relay: 1 }).level).toBe('relay');
        expect(classifyConnectivity({ host: 3, srflx: 2 }).level).toBe('stun');
        expect(classifyConnectivity({ host: 3 }).level).toBe('host');
        expect(classifyConnectivity({}).level).toBe('none');
    });
    it('names a user-facing i18n key for each verdict', () => {
        expect(classifyConnectivity({ relay: 1 }).i18nKey).toBe('conn.test.relay');
        expect(classifyConnectivity({ srflx: 1 }).i18nKey).toBe('conn.test.stun');
        expect(classifyConnectivity({ host: 1 }).i18nKey).toBe('conn.test.host');
        expect(classifyConnectivity({}).i18nKey).toBe('conn.test.none');
    });
});

describe('probeIceCandidates', () => {
    // A fake RTCPeerConnection that emits a scripted set of candidates then a null one.
    function FakePC(candidates) {
        return class {
            constructor() { this.onicecandidate = null; }
            createDataChannel() {}
            async createOffer() { return { type: 'offer', sdp: 'o' }; }
            async setLocalDescription() {
                // Emit candidates asynchronously, then the end-of-candidates null.
                Promise.resolve().then(() => {
                    for (const c of candidates) this.onicecandidate?.({ candidate: c });
                    this.onicecandidate?.({ candidate: null });
                });
            }
            close() {}
        };
    }

    it('counts candidates by type', async () => {
        const PC = FakePC([
            { type: 'host', candidate: 'x' },
            { type: 'srflx', candidate: 'x' },
            { type: 'srflx', candidate: 'x' },
            { type: 'relay', candidate: 'x' },
        ]);
        const counts = await probeIceCandidates([], { PC, timeoutMs: 500 });
        expect(counts).toEqual({ host: 1, srflx: 2, relay: 1, prflx: 0 });
    });

    it('returns zero counts when no PC is available', async () => {
        const counts = await probeIceCandidates([], { PC: null, timeoutMs: 10 });
        expect(counts).toEqual({ host: 0, srflx: 0, relay: 0, prflx: 0 });
    });
});
