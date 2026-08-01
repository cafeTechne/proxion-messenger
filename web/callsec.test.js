// callsec.test.js — end-to-end call authentication (R79 W3). Real WebCrypto Ed25519.
import { describe, it, expect, beforeAll } from 'vitest';
import { webcrypto } from 'node:crypto';
import {
    extractFingerprint, signFingerprint, verifyFingerprint, classifyPeerSdp, ed25519PubToDid,
} from './callsec.js';

// Node exposes WebCrypto; the module uses the global `crypto`.
beforeAll(() => { if (!globalThis.crypto) globalThis.crypto = webcrypto; });

const FP = 'sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99';
const sdp = (fp = FP) => `v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\na=fingerprint:${fp}\r\na=setup:actpass\r\n`;

async function identity() {
    const kp = await webcrypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
    const raw = new Uint8Array(await webcrypto.subtle.exportKey('raw', kp.publicKey));
    return { priv: kp.privateKey, did: ed25519PubToDid(raw) };
}

describe('extractFingerprint', () => {
    it('pulls a normalized fingerprint from SDP', () => {
        expect(extractFingerprint(sdp())).toBe(FP);
        expect(extractFingerprint('v=0\r\nno fp here')).toBeNull();
        expect(extractFingerprint(null)).toBeNull();
    });
    it('normalizes algo to lower- and hex to upper-case', () => {
        expect(extractFingerprint('a=fingerprint:SHA-256 aa:bb')).toBe('sha-256 AA:BB');
    });
});

describe('sign / verify', () => {
    it('verifies a genuine signature and rejects tampering', async () => {
        const me = await identity();
        const sig = await signFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', privKey: me.priv });
        expect(await verifyFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: me.did })).toBe(true);
        // Tampered fingerprint.
        expect(await verifyFingerprint({ fingerprint: 'sha-256 00:00', sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: me.did })).toBe(false);
        // Wrong session (replay to another call).
        expect(await verifyFingerprint({ fingerprint: FP, sessionId: 's2', role: 'offer', signatureB64: sig, signerDid: me.did })).toBe(false);
        // Wrong role (reflection).
        expect(await verifyFingerprint({ fingerprint: FP, sessionId: 's1', role: 'answer', signatureB64: sig, signerDid: me.did })).toBe(false);
    });

    it('rejects a signature from a different identity', async () => {
        const me = await identity();
        const other = await identity();
        const sig = await signFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', privKey: me.priv });
        expect(await verifyFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: other.did })).toBe(false);
    });
});

describe('classifyPeerSdp (call trust decision)', () => {
    it('is verified when the expected contact signed the real fingerprint', async () => {
        const peer = await identity();
        const sig = await signFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', privKey: peer.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: peer.did, expectedDid: peer.did,
        })).toBe('verified');
    });

    it('is a mismatch when the gateway swaps the fingerprint (MitM)', async () => {
        const peer = await identity();
        // Peer signed the REAL fingerprint, but the SDP we received was rewritten.
        const sig = await signFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', privKey: peer.priv });
        expect(await classifyPeerSdp({
            sdp: sdp('sha-256 DE:AD:BE:EF'), sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: peer.did, expectedDid: peer.did,
        })).toBe('mismatch');
    });

    it('is a mismatch when a known contact is impersonated by another key', async () => {
        const peer = await identity();
        const attacker = await identity();
        const sig = await signFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', privKey: attacker.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: attacker.did, expectedDid: peer.did,
        })).toBe('mismatch');
    });

    it('is a mismatch when a known contact call carries no signature at all', async () => {
        const peer = await identity();
        expect(await classifyPeerSdp({
            sdp: sdp(), sessionId: 's1', role: 'offer', signatureB64: '', signerDid: '', expectedDid: peer.did,
        })).toBe('mismatch');
    });

    it('is unverifiable for an unknown peer with no signature', async () => {
        expect(await classifyPeerSdp({
            sdp: sdp(), sessionId: 's1', role: 'offer', signatureB64: '', signerDid: '', expectedDid: '',
        })).toBe('unverifiable');
    });

    it('is unverifiable when there is no fingerprint to bind', async () => {
        const peer = await identity();
        expect(await classifyPeerSdp({
            sdp: 'v=0\r\nno fp', sessionId: 's1', role: 'offer', signatureB64: 'x', signerDid: peer.did, expectedDid: peer.did,
        })).toBe('unverifiable');
    });
});
