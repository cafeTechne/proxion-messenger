// callsec.test.js — end-to-end call authentication (R79 W3). Real WebCrypto Ed25519.
import { describe, it, expect, beforeAll } from 'vitest';
import { webcrypto } from 'node:crypto';
import {
    extractFingerprint, signFingerprint, verifyFingerprint, classifyPeerSdp, ed25519PubToDid,
} from './callsec.js';
import { issueDeviceCert } from './device-cert.js';

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
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: me.priv });
        expect(await verifyFingerprint({ fingerprint: FP, role: 'offer', signatureB64: sig, signerDid: me.did })).toBe(true);
        // Tampered fingerprint (this is what actually prevents replay: the fingerprint
        // is fresh per call, so an old signature never matches a new call's SDP).
        expect(await verifyFingerprint({ fingerprint: 'sha-256 00:00', role: 'offer', signatureB64: sig, signerDid: me.did })).toBe(false);
        // Wrong role (reflection of an offer signature as an answer).
        expect(await verifyFingerprint({ fingerprint: FP, role: 'answer', signatureB64: sig, signerDid: me.did })).toBe(false);
    });

    it('rejects a signature from a different identity', async () => {
        const me = await identity();
        const other = await identity();
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: me.priv });
        expect(await verifyFingerprint({ fingerprint: FP, role: 'offer', signatureB64: sig, signerDid: other.did })).toBe(false);
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

    it('is unverifiable (not refused) when a signer cannot be bound and offers no cert', async () => {
        // A different signing key with no cert to chain it to the contact: we cannot
        // prove it is them, but neither can we prove an attack (this is also the shape
        // of a cross-gateway call from a client that predates gateway delegation), so
        // it connects Unverified rather than being refused.
        const peer = await identity();
        const other = await identity();
        const sig = await signFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', privKey: other.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: other.did, expectedDid: peer.did,
        })).toBe('unverifiable');
    });

    it('is unverifiable when a known contact call carries no signature at all', async () => {
        // No signature: an older/non-signing peer. Allowed (Unverified), not refused.
        const peer = await identity();
        expect(await classifyPeerSdp({
            sdp: sdp(), sessionId: 's1', role: 'offer', signatureB64: '', signerDid: '', expectedDid: peer.did,
        })).toBe('unverifiable');
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

    it('is a mismatch when the SDP carries divergent fingerprints (R80 A4)', async () => {
        const peer = await identity();
        // Peer signs the real fingerprint, but the received SDP has a SECOND, different
        // fingerprint on another m-line (media redirected).
        const sig = await signFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', privKey: peer.priv });
        const split = `v=0\r\na=fingerprint:${FP}\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=fingerprint:sha-256 DE:AD:BE:EF\r\n`;
        expect(await classifyPeerSdp({
            sdp: split, sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: peer.did, expectedDid: peer.did,
        })).toBe('mismatch');
    });

    it('accepts identical fingerprints repeated per m-line (normal bundled call)', async () => {
        const peer = await identity();
        const sig = await signFingerprint({ fingerprint: FP, sessionId: 's1', role: 'offer', privKey: peer.priv });
        const bundled = `v=0\r\na=fingerprint:${FP}\r\nm=audio 9 x\r\na=fingerprint:${FP}\r\nm=video 9 x\r\na=fingerprint:${FP}\r\n`;
        expect(await classifyPeerSdp({
            sdp: bundled, sessionId: 's1', role: 'offer', signatureB64: sig, signerDid: peer.did, expectedDid: peer.did,
        })).toBe('verified');
    });
});

// R85 Track 1: a contact on a LINKED device signs with that device's key, not the
// account key we know them by. A device->account cert lets us accept it, so Verified
// works across devices and gateways without weakening the MitM refusal.
describe('classifyPeerSdp with a linked-device cert', () => {
    it('is verified when a valid device cert chains the signer to the expected account', async () => {
        const account = await identity();       // the contact as we know them
        const device = await identity();        // their linked device (signs the call)
        const cert = await issueDeviceCert(account.priv, account.did, device.did);
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: device.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), role: 'offer', signatureB64: sig,
            signerDid: device.did, expectedDid: account.did, deviceCert: cert,
        })).toBe('verified');
    });

    it('is unverifiable when the device signs but ships NO cert (cannot bind, not refused)', async () => {
        const account = await identity();
        const device = await identity();
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: device.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), role: 'offer', signatureB64: sig,
            signerDid: device.did, expectedDid: account.did, // no deviceCert
        })).toBe('unverifiable');
    });

    it('is a mismatch when the cert is for a DIFFERENT account (forged binding)', async () => {
        const account = await identity();
        const attackerAccount = await identity();
        const device = await identity();
        // Cert binds the device to the attacker's account, not the contact we expect.
        const cert = await issueDeviceCert(attackerAccount.priv, attackerAccount.did, device.did);
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: device.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), role: 'offer', signatureB64: sig,
            signerDid: device.did, expectedDid: account.did, deviceCert: cert,
        })).toBe('mismatch');
    });

    it('is a mismatch when the cert authorizes a different device than the signer', async () => {
        const account = await identity();
        const device = await identity();
        const otherDevice = await identity();
        // Valid cert, but for otherDevice; the signer is `device`, so it must not pass.
        const cert = await issueDeviceCert(account.priv, account.did, otherDevice.did);
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: device.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), role: 'offer', signatureB64: sig,
            signerDid: device.did, expectedDid: account.did, deviceCert: cert,
        })).toBe('mismatch');
    });

    it('is a mismatch when the device cert has expired', async () => {
        const account = await identity();
        const device = await identity();
        // Issued and expired in the past.
        const nowPast = Math.floor(Date.now() / 1000) - 10 * 86400;
        const cert = await issueDeviceCert(account.priv, account.did, device.did, { ttlDays: 1, now: nowPast });
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: device.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), role: 'offer', signatureB64: sig,
            signerDid: device.did, expectedDid: account.did, deviceCert: cert,
        })).toBe('mismatch');
    });
});

// R86: the SAME unbindable call is allowed for a peer we cannot confirm binds calls,
// but refused as a downgrade for a peer we know does (a stripped/absent binding proof).
describe('classifyPeerSdp peer-aware downgrade (R86)', () => {
    it('a stripped cert is unverifiable for a non-capable peer, downgrade for a capable one', async () => {
        const account = await identity();
        const device = await identity();
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: device.priv });
        const base = { sdp: sdp(), role: 'offer', signatureB64: sig, signerDid: device.did, expectedDid: account.did };
        expect(await classifyPeerSdp({ ...base, peerBindsCalls: false })).toBe('unverifiable');
        expect(await classifyPeerSdp({ ...base, peerBindsCalls: true })).toBe('downgrade');
    });

    it('no signature at all is unverifiable normally, downgrade for a capable peer', async () => {
        const account = await identity();
        const base = { sdp: sdp(), role: 'offer', signatureB64: '', signerDid: '', expectedDid: account.did };
        expect(await classifyPeerSdp({ ...base, peerBindsCalls: false })).toBe('unverifiable');
        expect(await classifyPeerSdp({ ...base, peerBindsCalls: true })).toBe('downgrade');
    });

    it('peerBindsCalls does NOT change a genuinely verified call', async () => {
        const account = await identity();
        const device = await identity();
        const cert = await issueDeviceCert(account.priv, account.did, device.did);
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: device.priv });
        expect(await classifyPeerSdp({
            sdp: sdp(), role: 'offer', signatureB64: sig,
            signerDid: device.did, expectedDid: account.did, deviceCert: cert, peerBindsCalls: true,
        })).toBe('verified');
    });

    it('peerBindsCalls does NOT downgrade a real tamper to something softer (still mismatch)', async () => {
        // Bound signer, bad fingerprint sig = tampered channel, refused as mismatch
        // regardless of the capability flag.
        const account = await identity();
        expect(await classifyPeerSdp({
            sdp: sdp(), role: 'offer', signatureB64: 'bogus',
            signerDid: account.did, expectedDid: account.did, peerBindsCalls: true,
        })).toBe('mismatch');
    });
});
