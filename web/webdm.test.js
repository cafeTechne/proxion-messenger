import { describe, it, expect, vi } from 'vitest';
import { createWebDm, peerPodRootFromWebId, envelopeFromDmPayload, isDmPeerWebId } from './webdm.js';

describe('peerPodRootFromWebId', () => {
    it('derives the pod root from a CSS-style WebID', () => {
        expect(peerPodRootFromWebId('https://pod.example/alice/profile/card#me'))
            .toBe('https://pod.example/alice/');
    });
    it('falls back to the origin when there is no profile path', () => {
        expect(peerPodRootFromWebId('https://alice.example/#me')).toBe('https://alice.example/');
    });
    it('returns null for empty input', () => {
        expect(peerPodRootFromWebId('')).toBe(null);
    });
});

describe('envelopeFromDmPayload', () => {
    it('carries the ciphertext and ratchet fields, tagged with the sender', () => {
        const env = envelopeFromDmPayload({
            message_id: 'm1', content: 'CT', e2e: true, nonce: 'N', msg_num: 3, pn: 1,
            ratchet_pub: 'RP', x25519_pub: 'XP', reply_to_id: 'r0',
        }, 'https://me', 'Me');
        expect(env).toMatchObject({
            v: 1, from_webid: 'https://me', from_display_name: 'Me', message_id: 'm1',
            content: 'CT', e2e: true, nonce: 'N', msg_num: 3, pn: 1, ratchet_pub: 'RP',
            x25519_pub: 'XP', reply_to_id: 'r0',
        });
    });
});

function harness({ drops = [], myDevice = 'dev-me', persistMessage, signEnvelope, verifySender, isBlocked, handleEvent } = {}) {
    const events = [];
    const dropped = [];
    const deleted = [];
    const persisted = [];
    const pod = {
        podEnsureDmInbox: vi.fn(async () => 'https://me/proxion/dm-inbox/'),
        podDropDm: vi.fn(async (root, env) => { dropped.push({ root, env }); return true; }),
        podReadDmDrops: vi.fn(async () => drops),
        podDeleteDmDrop: vi.fn(async (url) => { deleted.push(url); return true; }),
    };
    const e2e = {
        cachePeerPub: vi.fn(),
        ratchetDecrypt: vi.fn(async (from, ct) => 'plain:' + ct),
    };
    const notify = { watchResource: vi.fn(() => () => {}) };
    const dm = createWebDm({
        pod, e2e, notify, handleEvent: handleEvent || ((e) => events.push(e)),
        getSelfWebId: () => 'https://me/profile/card#me', getDisplayName: () => 'Me',
        getMyDeviceId: () => myDevice,
        persistMessage: persistMessage && ((m) => { persisted.push(m); return persistMessage(m); }),
        signEnvelope, verifySender, isBlocked,
    });
    return { dm, pod, e2e, notify, events, dropped, deleted, persisted };
}

describe('createWebDm.dropDm', () => {
    it('drops an encrypted envelope to the recipient pod root', async () => {
        const { dm, dropped } = harness();
        const ok = await dm.dropDm({
            cmd: 'local_dm', target_webid: 'https://pod.example/bob/profile/card#me',
            message_id: 'm1', content: 'CT', e2e: true, nonce: 'N', ratchet_pub: 'RP', x25519_pub: 'XP',
        });
        expect(ok).toBe(true);
        expect(dropped).toHaveLength(1);
        expect(dropped[0].root).toBe('https://pod.example/bob/');
        expect(dropped[0].env).toMatchObject({ from_webid: 'https://me/profile/card#me', content: 'CT', e2e: true });
    });

    it('returns false without a recipient', async () => {
        const { dm, dropped } = harness();
        expect(await dm.dropDm({ cmd: 'local_dm', message_id: 'm' })).toBe(false);
        expect(dropped).toHaveLength(0);
    });

    it('signs the envelope when signEnvelope is provided', async () => {
        const { dm, dropped } = harness({ signEnvelope: async () => ({ signer: 'did:key:zSIGNER', sig: 'SIG' }) });
        await dm.dropDm({ cmd: 'local_dm', target_webid: 'https://pod.example/bob/profile/card#me', message_id: 'm', content: 'CT' });
        expect(dropped[0].env).toMatchObject({ signer: 'did:key:zSIGNER', sig: 'SIG' });
    });
});

describe('createWebDm sender verification', () => {
    it('marks a received DM verified when verifySender approves', async () => {
        const { dm, events } = harness({
            verifySender: async () => true,
            drops: [{ url: 'u', envelope: { from_webid: 'https://bob', message_id: 'm', content: 'hi', e2e: false, signer: 'did:key:z', sig: 'S' } }],
        });
        await dm.drainOnce();
        expect(events[0].sender_verified).toBe(true);
    });

    it('marks a received DM unverified when verifySender rejects', async () => {
        const { dm, events } = harness({
            verifySender: async () => false,
            drops: [{ url: 'u', envelope: { from_webid: 'https://bob', message_id: 'm', content: 'hi', e2e: false } }],
        });
        await dm.drainOnce();
        expect(events[0].sender_verified).toBe(false);
    });
});

describe('createWebDm.dropFanout', () => {
    it('drops one envelope per recipient/own device to their pods', async () => {
        const { dm, dropped } = harness();
        const ok = await dm.dropFanout({
            cmd: 'send_dm_fanout', message_id: 'm1', fanout: [
                { to_webid: 'https://bob.example/profile/card#me', to_device_id: 'bob-1', payload: { content: 'CT1', e2e: true } },
                { to_webid: 'https://me.example/profile/card#me', to_device_id: 'my-2', payload: { content: 'CT2', e2e: true } },
            ],
        });
        expect(ok).toBe(true);
        expect(dropped).toHaveLength(2);
        expect(dropped[0]).toMatchObject({ root: 'https://bob.example/' });
        expect(dropped[0].env).toMatchObject({ kind: 'fanout', message_id: 'm1', to_device_id: 'bob-1', from_webid: 'https://me/profile/card#me' });
        expect(dropped[1].env.to_device_id).toBe('my-2');
    });

    it('does not report delivery when only self-sync copies dropped', async () => {
        // A fanout to our OWN other devices only (single-device peer path) must not
        // clear the message's pending state: no recipient device received it.
        const { dm, dropped } = harness();
        const ok = await dm.dropFanout({
            cmd: 'send_dm_fanout', message_id: 'm2', fanout: [
                { to_webid: 'https://me/profile/card#me', to_device_id: 'my-2', payload: { content: 'CT' } },
            ],
        });
        expect(ok).toBe(false);
        expect(dropped).toHaveLength(1);   // the self-sync copy still went out
    });

    it('reports delivery when at least one recipient device got a copy', async () => {
        const { dm } = harness();
        const ok = await dm.dropFanout({
            cmd: 'send_dm_fanout', message_id: 'm3', fanout: [
                { to_webid: 'https://bob.example/profile/card#me', to_device_id: 'bob-1', payload: { content: 'CT' } },
                { to_webid: 'https://me/profile/card#me', to_device_id: 'my-2', payload: { content: 'CT' } },
            ],
        });
        expect(ok).toBe(true);
    });
});

describe('createWebDm fanout receive', () => {
    it('claims the fanout copy for this device and dispatches dm_fanout', async () => {
        const { dm, events, deleted } = harness({ myDevice: 'dev-me', verifySender: async () => true, drops: [
            { url: 'u1', envelope: { kind: 'fanout', from_webid: 'https://bob', message_id: 'm', to_device_id: 'dev-me', payload: { content: 'CT', e2e: true, from_device_id: 'bob-1' } } },
        ] });
        await dm.drainOnce();
        expect(events[0]).toMatchObject({ type: 'dm_fanout', to_device_id: 'dev-me', from_webid: 'https://bob', message_id: 'm' });
        expect(deleted).toEqual(['u1']);
    });

    it('leaves a sibling device fanout copy untouched', async () => {
        const { dm, events, deleted } = harness({ myDevice: 'dev-me', drops: [
            { url: 'sib', envelope: { kind: 'fanout', from_webid: 'https://bob', message_id: 'm', to_device_id: 'other-device', payload: {} } },
        ] });
        await dm.drainOnce();
        expect(events).toHaveLength(0);
        expect(deleted).toHaveLength(0);
    });
});

describe('createWebDm receive', () => {
    it('decrypts a drop, emits a message event, and deletes it', async () => {
        const { dm, e2e, events, deleted } = harness({ verifySender: async () => true, drops: [
            { url: 'https://me/proxion/dm-inbox/d1', envelope: {
                from_webid: 'https://bob', message_id: 'm9', content: 'CT9', e2e: true,
                nonce: 'N', msg_num: 0, pn: 0, ratchet_pub: 'RP', x25519_pub: 'XP',
                signer: 'did:key:z', sig: 'S',
            } },
        ] });
        await dm.drainOnce();
        expect(e2e.cachePeerPub).toHaveBeenCalledWith('https://bob', 'XP');
        expect(e2e.ratchetDecrypt).toHaveBeenCalledWith('https://bob', 'CT9', 'N', 0, 'RP', 0, { allowInit: true });
        expect(events).toHaveLength(1);
        expect(events[0]).toMatchObject({
            type: 'message', message_id: 'm9', thread_id: 'https://bob',
            from_webid: 'https://bob', content: 'plain:CT9', source: 'local_dm', _persistDm: true,
        });
        expect(deleted).toEqual(['https://me/proxion/dm-inbox/d1']);
    });

    it('does NOT cache the peer pub for an unverified e2e drop (first-contact MitM)', async () => {
        // An attacker can drop a signed-looking e2e envelope into our public-Append
        // inbox. If verifySender rejects it, its x25519_pub must not seed the trusted
        // send-side key cache, or our first reply would encrypt under the wrong key.
        const { dm, e2e } = harness({ verifySender: async () => false, drops: [
            { url: 'u', envelope: {
                from_webid: 'https://mallory', message_id: 'm', content: 'CT', e2e: true,
                nonce: 'N', msg_num: 0, pn: 0, ratchet_pub: 'RP', x25519_pub: 'ATTACKER_XP',
            } },
        ] });
        await dm.drainOnce();
        expect(e2e.cachePeerPub).not.toHaveBeenCalled();
    });

    it('does NOT cache the peer pub when no verifier is available', async () => {
        const { dm, e2e } = harness({ drops: [
            { url: 'u', envelope: {
                from_webid: 'https://bob', message_id: 'm', content: 'CT', e2e: true,
                nonce: 'N', msg_num: 0, pn: 0, ratchet_pub: 'RP', x25519_pub: 'XP',
            } },
        ] });
        await dm.drainOnce();
        expect(e2e.cachePeerPub).not.toHaveBeenCalled();
    });

    it('leaves an undecryptable drop in place (no delete, no event)', async () => {
        const { dm, e2e, events, deleted } = harness({ drops: [
            { url: 'https://me/proxion/dm-inbox/bad', envelope: {
                from_webid: 'https://bob', message_id: 'mX', content: 'CT', e2e: true, ratchet_pub: 'RP',
            } },
        ] });
        e2e.ratchetDecrypt.mockRejectedValueOnce(new Error('bad ratchet'));
        await dm.drainOnce();
        expect(events).toHaveLength(0);
        expect(deleted).toHaveLength(0);
    });

    it('passes non-e2e drops through as plaintext', async () => {
        const { dm, e2e, events } = harness({ drops: [
            { url: 'u', envelope: { from_webid: 'https://bob', message_id: 'm', content: 'hello', e2e: false } },
        ] });
        await dm.drainOnce();
        expect(e2e.ratchetDecrypt).not.toHaveBeenCalled();
        expect(events[0].content).toBe('hello');
    });

    it('renders and persists to history, then deletes the drop', async () => {
        const { dm, events, deleted, persisted } = harness({
            persistMessage: async () => true,
            drops: [{ url: 'u1', envelope: { from_webid: 'https://bob', message_id: 'm', content: 'hi', e2e: false } }],
        });
        await dm.drainOnce();
        expect(persisted).toHaveLength(1);
        expect(persisted[0]).toMatchObject({ message_id: 'm', thread_id: 'https://bob', content: 'hi' });
        expect(events).toHaveLength(1);
        expect(deleted).toEqual(['u1']);
    });

    it('still renders and deletes when the best-effort history write fails', async () => {
        // A decrypt advances the ratchet, so the message can never be re-decrypted:
        // a cache-write failure must NOT hide it or wedge the drop.
        const { dm, events, deleted } = harness({
            persistMessage: async () => false,   // e.g. IndexedDB quota / private mode
            drops: [{ url: 'u2', envelope: { from_webid: 'https://bob', message_id: 'm', content: 'hi', e2e: false } }],
        });
        await dm.drainOnce();
        expect(events).toHaveLength(1);
        expect(deleted).toEqual(['u2']);
    });

    it('start() ensures the inbox, drains, and subscribes', async () => {
        const { dm, pod, notify } = harness();
        await dm.start();
        expect(pod.podEnsureDmInbox).toHaveBeenCalled();
        expect(pod.podReadDmDrops).toHaveBeenCalled();
        expect(notify.watchResource).toHaveBeenCalledWith('https://me/proxion/dm-inbox/', expect.any(Function));
    });
});

function deferred() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return { promise, resolve };
}

describe('createWebDm inbox concurrency and acknowledgement', () => {
    const encryptedDrop = (id = 'm1') => ({ url: 'https://me/inbox/' + id, envelope: {
        from_webid: 'https://bob', message_id: id, content: 'CT-' + id, e2e: true,
        nonce: 'N', msg_num: 0, pn: 0, ratchet_pub: 'RP',
    } });

    it('serializes overlapping scans, rescans arrivals, and never decrypts a stale drop twice', async () => {
        const firstDecrypt = deferred();
        const entered = deferred();
        const first = encryptedDrop(), second = encryptedDrop('m2');
        const h = harness({ verifySender: async () => true });
        h.pod.podReadDmDrops.mockResolvedValueOnce([first]).mockResolvedValue([first, second]);
        h.e2e.ratchetDecrypt.mockImplementationOnce(async () => {
            entered.resolve();
            await firstDecrypt.promise;
            return 'first';
        });
        const one = h.dm.drainOnce();
        await entered.promise;
        const two = h.dm.drainOnce(), three = h.dm.drainOnce();
        expect(h.pod.podReadDmDrops).toHaveBeenCalledTimes(1);
        firstDecrypt.resolve();
        await Promise.all([one, two, three]);
        expect(h.pod.podReadDmDrops).toHaveBeenCalledTimes(2);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(2);
        expect(h.events.map(e => e.message_id)).toEqual(['m1', 'm2']);
        await h.dm.drainOnce();
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(2);
        expect(h.pod.podDeleteDmDrop).toHaveBeenCalledTimes(2);
    });

    it.each(['false', 'throw'])('retries a %s deletion without spending another ratchet key', async failure => {
        const h = harness({ drops: [encryptedDrop()], verifySender: async () => true });
        if (failure === 'throw') h.pod.podDeleteDmDrop.mockRejectedValueOnce(new Error('offline'));
        else h.pod.podDeleteDmDrop.mockResolvedValueOnce(false);
        await h.dm.drainOnce();
        await h.dm.drainOnce();
        expect(h.pod.podDeleteDmDrop).toHaveBeenCalledTimes(2);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
        expect(h.events).toHaveLength(1);
    });

    it('continues persistence and acknowledgement when the asynchronous UI handler rejects', async () => {
        const h = harness({ drops: [encryptedDrop()], verifySender: async () => true,
            handleEvent: async () => { throw new Error('UI failed'); }, persistMessage: async () => true });
        await h.dm.drainOnce();
        expect(h.persisted).toHaveLength(1);
        expect(h.deleted).toEqual(['https://me/inbox/m1']);
    });

    it('can retry a failed scan and a failed decrypt', async () => {
        const h = harness({ drops: [encryptedDrop()], verifySender: async () => true });
        h.pod.podReadDmDrops.mockRejectedValueOnce(new Error('offline'));
        await expect(h.dm.drainOnce()).rejects.toThrow('offline');
        h.e2e.ratchetDecrypt.mockRejectedValueOnce(new Error('temporary'));
        await h.dm.drainOnce();
        expect(h.deleted).toEqual([]);
        await h.dm.drainOnce();
        expect(h.events).toHaveLength(1);
        expect(h.deleted).toHaveLength(1);
    });

    it('watches before listing and shares concurrent startup', async () => {
        const listing = deferred(), entered = deferred();
        const h = harness({ verifySender: async () => true });
        h.pod.podReadDmDrops.mockImplementationOnce(() => {
            expect(h.notify.watchResource).toHaveBeenCalledTimes(1);
            entered.resolve();
            return listing.promise;
        }).mockResolvedValue([encryptedDrop()]);
        const one = h.dm.start(), two = h.dm.start();
        await entered.promise;
        h.notify.watchResource.mock.calls[0][1]();
        listing.resolve([]);
        await Promise.all([one, two]);
        expect(h.pod.podEnsureDmInbox).toHaveBeenCalledTimes(1);
        expect(h.notify.watchResource).toHaveBeenCalledTimes(1);
        expect(h.events).toHaveLength(1);
    });

    it('does not install a late watcher after stop during startup', async () => {
        const inbox = deferred();
        const h = harness();
        h.pod.podEnsureDmInbox.mockReturnValue(inbox.promise);
        const start = h.dm.start();
        await Promise.resolve();
        h.dm.stop();
        inbox.resolve('https://me/inbox/');
        expect(await start).toBe(null);
        expect(h.notify.watchResource).not.toHaveBeenCalled();
        expect(h.pod.podReadDmDrops).not.toHaveBeenCalled();
    });

    it('ignores a previously queued notification after stop', async () => {
        const h = harness();
        await h.dm.start();
        const callback = h.notify.watchResource.mock.calls[0][1];
        h.dm.stop();
        callback();
        await Promise.resolve();
        expect(h.pod.podReadDmDrops).toHaveBeenCalledTimes(1);
    });

    it('awaits fanout delivery and retries a failed acknowledgement without dispatching twice', async () => {
        const delivery = deferred(), entered = deferred();
        const handleEvent = vi.fn(async () => { entered.resolve(); await delivery.promise; });
        const h = harness({ handleEvent, verifySender: async () => true, drops: [{
            url: 'fanout', envelope: { kind: 'fanout', from_webid: 'https://bob',
                message_id: 'm', to_device_id: 'dev-me', payload: { e2e: true } },
        }] });
        h.pod.podDeleteDmDrop.mockResolvedValueOnce(false);
        const first = h.dm.drainOnce();
        await entered.promise;
        const next = h.dm.drainOnce();
        expect(h.pod.podDeleteDmDrop).not.toHaveBeenCalled();
        delivery.resolve();
        await Promise.all([first, next]);
        expect(handleEvent).toHaveBeenCalledTimes(1);
        expect(h.pod.podDeleteDmDrop).toHaveBeenCalledTimes(2);
    });

    it('a restart waits out the stopped listing and then reads the inbox anew', async () => {
        const listing = deferred(), entered = deferred();
        const h = harness({ verifySender: async () => true });
        h.pod.podReadDmDrops.mockImplementationOnce(() => {
            entered.resolve();
            return listing.promise;
        }).mockResolvedValue([encryptedDrop()]);
        const first = h.dm.start();
        await entered.promise;
        h.dm.stop();
        const second = h.dm.start();
        listing.resolve([encryptedDrop('old')]);
        await Promise.all([first, second]);
        expect(h.events.map(e => e.message_id)).toEqual(['m1']);
        expect(h.pod.podReadDmDrops).toHaveBeenCalledTimes(2);
        expect(h.notify.watchResource).toHaveBeenCalledTimes(2);
    });

    it('retains failed acknowledgements at the cap, leaving new ciphertext untouched until space frees', async () => {
        const drops = Array.from({ length: 1025 }, (_, i) => encryptedDrop(String(i)));
        const h = harness({ drops, verifySender: async () => true });
        h.pod.podDeleteDmDrop.mockResolvedValue(false);
        await h.dm.drainOnce();
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1024);
        h.pod.podDeleteDmDrop.mockResolvedValue(true);
        await h.dm.drainOnce();
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1025);
        expect(h.events.at(-1).message_id).toBe('1024');
    });
    it('reconciles live-only receipts after external removal without holding the memory cap forever', async () => {
        const drops = Array.from({ length: 1025 }, (_, index) => encryptedDrop(String(index)));
        const h = harness({ drops, verifySender: async () => true });
        h.pod.podDeleteDmDrop.mockResolvedValue(false);
        await h.dm.drainOnce();
        expect(h.events).toHaveLength(1024);
        h.pod.podDmDropExists = vi.fn(async () => false);
        h.pod.podReadDmDrops.mockResolvedValue([drops.at(-1)]);
        await h.dm.drainOnce();
        expect(h.pod.podDmDropExists).toHaveBeenCalledTimes(8);
        expect(h.events).toHaveLength(1025);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1025);
    });
});

describe('isDmPeerWebId', () => {
    it('accepts https WebIDs and http only on a loopback host', () => {
        expect(isDmPeerWebId('https://pod.example/alice/profile/card#me')).toBe(true);
        expect(isDmPeerWebId('http://localhost:3000/alice/profile/card#me')).toBe(true);
        expect(isDmPeerWebId('http://127.0.0.1:3000/alice/profile/card#me')).toBe(true);
        expect(isDmPeerWebId('http://pod.example/alice/profile/card#me')).toBe(false);
        expect(isDmPeerWebId('https://pod.example/a b')).toBe(false);
        expect(isDmPeerWebId('https://pod.example/<x>')).toBe(false);
        expect(isDmPeerWebId('room-123')).toBe(false);
        expect(isDmPeerWebId('')).toBe(false);
        expect(isDmPeerWebId(null)).toBe(false);
        expect(isDmPeerWebId('https://' + 'a'.repeat(3000))).toBe(false);
    });
    it('refuses gateway links that are not a profile', () => {
        expect(isDmPeerWebId('https://gw.example:8080/i/645ad6afb53f32ba')).toBe(false);
        expect(isDmPeerWebId('https://gw.example/invite?from=did%3Akey%3Az6Mk')).toBe(false);
        expect(isDmPeerWebId('https://gw.example/?join=abc#x')).toBe(false);
        expect(isDmPeerWebId('https://gw.example:8080/')).toBe(false);
        expect(isDmPeerWebId('https://alice.example/#me')).toBe(true);
        expect(isDmPeerWebId('https://alice.example/profile/card')).toBe(true);
    });
});

describe('createWebDm blocks', () => {
    it('drops a DM from a blocked WebID unread, without decrypting it', async () => {
        const from = 'https://pod.example/eve/profile/card#me';
        const { dm, e2e, events, deleted } = harness({
            drops: [{ url: 'u1', envelope: { from_webid: from, message_id: 'm1', content: 'CT', e2e: true, nonce: 'N' } },
                { url: 'u2', envelope: { kind: 'fanout', from_webid: from, message_id: 'm2', to_device_id: 'dev-me', payload: {} } }],
            verifySender: async () => 'verified',
            isBlocked: (w) => w === from,
        });
        await dm.drainOnce();
        expect(e2e.ratchetDecrypt).not.toHaveBeenCalled();
        expect(events).toEqual([]);
        expect(deleted).toEqual(['u1', 'u2']);
    });

    it('still delivers from a WebID that is not blocked', async () => {
        const { dm, events } = harness({
            drops: [{ url: 'u1', envelope: { from_webid: 'https://pod.example/bob/profile/card#me', message_id: 'm1', content: 'hi' } }],
            verifySender: async () => 'verified',
            isBlocked: () => false,
        });
        await dm.drainOnce();
        expect(events).toHaveLength(1);
    });
});
