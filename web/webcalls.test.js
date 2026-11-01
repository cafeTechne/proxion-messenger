import { describe, it, expect, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { createWebCalls, SIGNAL_CMDS, callPeerWebId, createCallSigners, UNRESOLVED_SIGNER } from './webcalls.js';
import { signFingerprint, classifyPeerSdp, ed25519PubToDid } from './callsec.js';

function harness({ signals = [], isBlocked, isAllowedCaller } = {}) {
    const events = [];
    const dropped = [];
    const deleted = [];
    const pod = {
        podEnsureCallInbox: vi.fn(async () => 'https://me/proxion/call-inbox/'),
        podDropSignal: vi.fn(async (root, signal) => { dropped.push({ root, signal }); return true; }),
        podReadSignals: vi.fn(async () => signals),
        podDeleteSignal: vi.fn(async (url) => { deleted.push(url); return true; }),
    };
    const notify = { watchResource: vi.fn(() => () => {}), setFast: vi.fn() };
    const calls = createWebCalls({
        pod, notify, handleEvent: (e) => events.push(e),
        getSelfWebId: () => 'https://me/profile/card#me', getDisplayName: () => 'Me',
        peerPodRoot: (w) => w.replace(/profile\/card#me$/, ''),
        isBlocked, isAllowedCaller,
    });
    return { calls, pod, notify, events, dropped, deleted };
}

describe('SIGNAL_CMDS', () => {
    it('covers the voice.js signaling commands', () => {
        for (const c of ['voice_invite', 'voice_answer', 'ice_candidate', 'voice_hangup']) {
            expect(SIGNAL_CMDS.has(c)).toBe(true);
        }
    });
});

describe('createWebCalls.sendSignal', () => {
    it('drops a signal to the callee, stamped with the sender + type', async () => {
        const { calls, dropped } = harness();
        const ok = await calls.sendSignal({
            cmd: 'voice_invite', target_webid: 'https://bob.example/profile/card#me',
            sdp_offer: 'OFFER', fp_sig: 'SIG', fp_signer: 'SIGNER',
        });
        expect(ok).toBe(true);
        expect(dropped[0].root).toBe('https://bob.example/');
        expect(dropped[0].signal).toMatchObject({
            type: 'voice_invite', from_webid: 'https://me/profile/card#me',
            caller_webid: 'https://me/profile/card#me', sdp_offer: 'OFFER', fp_sig: 'SIG', fp_signer: 'SIGNER',
        });
        expect(dropped[0].signal.cmd).toBeUndefined();          // cmd renamed to type
        expect(dropped[0].signal.target_webid).toBeUndefined();  // recipient not echoed into the body
    });

    it('returns false without a target', async () => {
        const { calls, dropped } = harness();
        expect(await calls.sendSignal({ cmd: 'ice_candidate' })).toBe(false);
        expect(dropped).toHaveLength(0);
    });
});

describe('createWebCalls receive', () => {
    it('dispatches each signal as an event and deletes it', async () => {
        const { calls, events, deleted } = harness({ signals: [
            { url: 'u1', signal: { type: 'voice_invite', from_webid: 'https://bob', caller_webid: 'https://bob', sdp_offer: 'O' } },
            { url: 'u2', signal: { type: 'ice_candidate', from_webid: 'https://bob', candidate: 'C' } },
        ] });
        await calls.drainOnce();
        expect(events.map((e) => e.type)).toEqual(['voice_invite', 'ice_candidate']);
        expect(deleted).toEqual(['u1', 'u2']);
    });

    it('deletes a malformed signal without dispatching', async () => {
        const { calls, events, deleted } = harness({ signals: [{ url: 'bad', signal: { no: 'type' } }] });
        await calls.drainOnce();
        expect(events).toHaveLength(0);
        expect(deleted).toEqual(['bad']);
    });

    it('drops a non-voice event injected into the call inbox but keeps the voice one', async () => {
        // The call inbox is public-Append; a non-voice event forged in by anyone
        // who knows the WebID must never reach the client dispatch, yet the drop
        // is still cleaned up so it cannot be replayed.
        const { calls, events, deleted } = harness({ signals: [
            { url: 'evil1', signal: { type: 'contact_revoked', from_webid: 'https://mallory' } },
            { url: 'evil2', signal: { type: 'message', from_webid: 'https://mallory', content: 'spoofed' } },
            { url: 'ok', signal: { type: 'voice_invite', from_webid: 'https://bob', caller_webid: 'https://bob', sdp_offer: 'O' } },
        ] });
        await calls.drainOnce();
        expect(events.map((e) => e.type)).toEqual(['voice_invite']);
        expect(deleted).toEqual(['evil1', 'evil2', 'ok']);
    });

    it('start() ensures the inbox, drains, and subscribes', async () => {
        const { calls, pod, notify } = harness();
        await calls.start();
        expect(pod.podEnsureCallInbox).toHaveBeenCalled();
        expect(pod.podReadSignals).toHaveBeenCalled();
        expect(notify.watchResource).toHaveBeenCalledWith('https://me/proxion/call-inbox/', expect.any(Function), { fast: false });
    });
});

describe('createWebCalls blocks', () => {
    it('drops call signals from a blocked WebID without ringing', async () => {
        const eve = 'https://eve.example/profile/card#me';
        const { calls, events, deleted } = harness({
            signals: [
                { url: 's1', signal: { type: 'voice_invite', from_webid: eve, caller_webid: eve } },
                { url: 's2', signal: { type: 'voice_invite', from_webid: 'https://bob.example/profile/card#me' } },
            ],
            isBlocked: (w) => w === eve,
        });
        await calls.drainOnce();
        expect(events.map((e) => e.from_webid)).toEqual(['https://bob.example/profile/card#me']);
        expect(deleted).toEqual(['s1', 's2']);
    });
});

const BOB = 'https://bob.example/profile/card#me';
const MALLORY = 'https://mallory.example/profile/card#me';

describe('createWebCalls on the desktop', () => {
    it('watches the inbox fast only while ringing or in a call', async () => {
        const { calls, notify } = harness();
        calls.setActive(true);   // before start: the first watch is already fast
        await calls.start();
        expect(notify.watchResource).toHaveBeenCalledWith('https://me/proxion/call-inbox/', expect.any(Function), { fast: true });
        calls.setActive(false);
        expect(notify.setFast).toHaveBeenLastCalledWith('https://me/proxion/call-inbox/', false);
        calls.setActive(false);   // no change, no resend
        expect(notify.setFast).toHaveBeenCalledTimes(1);
        calls.setActive(true);
        expect(notify.setFast).toHaveBeenLastCalledWith('https://me/proxion/call-inbox/', true);
    });

    it('drops signals from callers that may not ring, but still deletes them', async () => {
        const { calls, events, deleted } = harness({
            isAllowedCaller: (w) => w === BOB,
            signals: [
                { url: 'stranger', signal: { type: 'voice_invite', from_webid: MALLORY, caller_webid: MALLORY, sdp_offer: 'O' } },
                { url: 'mixed', signal: { type: 'voice_invite', from_webid: BOB, caller_webid: MALLORY, sdp_offer: 'O' } },
                { url: 'nobody', signal: { type: 'voice_hangup', session_id: 's' } },
                { url: 'ok', signal: { type: 'voice_invite', from_webid: BOB, caller_webid: BOB, sdp_offer: 'O' } },
            ],
        });
        await calls.drainOnce();
        expect(events.map((e) => e.from_webid)).toEqual([BOB]);
        expect(deleted).toEqual(['stranger', 'mixed', 'nobody', 'ok']);
    });
});

describe('callPeerWebId', () => {
    it('prefers the WebID on the signal, then the open thread', () => {
        expect(callPeerWebId({ peerWebid: MALLORY }, { caller_webid: BOB })).toBe(BOB);
        expect(callPeerWebId({ peerWebid: BOB }, { from_webid: 'did:key:zGw' })).toBe(BOB);
        expect(callPeerWebId({ type: 'local_dm', id: BOB }, {})).toBe(BOB);
        expect(callPeerWebId({ peerWebid: 'did:key:zPeer' }, { caller_webid: 'did:key:zPeer' })).toBe(null);
        expect(callPeerWebId(null, null)).toBe(null);
    });
});

describe('createCallSigners', () => {
    it('reads the signer fresh, and falls back to the last one read', async () => {
        let answer = { signer: 'did:key:zOne' };
        const s = createCallSigners({ fetchSigner: vi.fn(async () => { if (answer instanceof Error) throw answer; return answer; }) });
        expect(await s.resolve(BOB)).toBe('did:key:zOne');
        answer = { signer: 'did:key:zTwo' };
        expect(await s.resolve(BOB)).toBe('did:key:zTwo');
        answer = new Error('offline');
        expect(await s.resolve(BOB)).toBe('did:key:zTwo');
        expect(s.cached(BOB)).toBe('did:key:zTwo');
    });

    it('ignores a signer that is not a did:key, and anyone who is not a WebID', async () => {
        const fetchSigner = vi.fn(async () => ({ signer: 'https://evil/' }));
        const s = createCallSigners({ fetchSigner });
        expect(await s.resolve(BOB)).toBe(null);
        expect(await s.resolve('did:key:zPeer')).toBe(null);
        expect(fetchSigner).toHaveBeenCalledTimes(1);
    });

    it('expects a placeholder no key matches when the signer is unknown', async () => {
        const s = createCallSigners({ fetchSigner: async () => null });
        expect(await s.expectedFor(null, { caller_webid: BOB })).toBe(UNRESOLVED_SIGNER + BOB);
        expect(await s.expectedFor(null, { caller_webid: 'did:key:zPeer' })).toBe('');
    });
});

// The badge reads Verified only for the key the contact's own pod names.
describe('a WebID call verifies against the signer.json of the contact', () => {
    if (!globalThis.crypto) globalThis.crypto = webcrypto;
    const FP = 'sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99';
    const SDP = 'v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\na=fingerprint:' + FP + '\r\na=setup:actpass\r\n';
    const ME = 'https://me/profile/card#me';
    async function key() {
        const kp = await webcrypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
        return { priv: kp.privateKey, did: ed25519PubToDid(new Uint8Array(await webcrypto.subtle.exportKey('raw', kp.publicKey))) };
    }
    async function verdict(signers, signer, extra = {}) {
        const sig = await signFingerprint({ fingerprint: FP, role: 'offer', privKey: signer.priv, recipientDid: ME });
        const event = { caller_webid: BOB, fp_sig: sig, fp_signer: signer.did, ...extra };
        return classifyPeerSdp({
            sdp: SDP, role: 'offer', signatureB64: sig, signerDid: signer.did,
            expectedDid: await signers.expectedFor(null, event), deviceCert: extra.fp_cert || null,
            recipientDid: [ME],
        });
    }

    it('Verified for the published signer, never for another key', async () => {
        const bob = await key(), forger = await key();
        const signers = createCallSigners({ fetchSigner: async () => ({ signer: bob.did }) });
        expect(await verdict(signers, bob)).toBe('verified');
        expect(await verdict(signers, forger)).not.toBe('verified');
        // A forged cert trying to tie the forger to Bob is refused outright.
        expect(await verdict(signers, forger, { fp_cert: { device_did: forger.did, account_did: bob.did, sig: 'x' } })).toBe('mismatch');
    });

    it('not Verified when the signer cannot be read', async () => {
        const forger = await key();
        const signers = createCallSigners({ fetchSigner: async () => { throw new Error('unreachable'); } });
        expect(await verdict(signers, forger)).toBe('unverifiable');
    });
});
