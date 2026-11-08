import { describe, it, expect, vi } from 'vitest';
import { createPodPins, pinTarget, normalizePinSnapshot, MAX_ROOM_PINS } from './room-pins.js';
import { signRoomPins } from './dmsig.js';
import { messageIriAt } from './longchat.js';
const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function b58encode(bytes) {
    let zeros = 0; while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
    const digits = [0];
    for (let i = zeros; i < bytes.length; i++) {
        let carry = bytes[i];
        for (let j = 0; j < digits.length; j++) { carry += digits[j] << 8; digits[j] = carry % 58; carry = (carry / 58) | 0; }
        while (carry > 0) { digits.push(carry % 58); carry = (carry / 58) | 0; }
    }
    let s = ''; for (let k = 0; k < zeros; k++) s += '1';
    for (let q = digits.length - 1; q >= 0; q--) s += B58[digits[q]];
    return s;
}
function didFromPub(pub) {
    const mc = new Uint8Array(2 + pub.length); mc[0] = 0xed; mc[1] = 0x01; mc.set(pub, 2);
    return 'did:key:z' + b58encode(mc);
}
async function makeIdentity() {
    const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
    const rawPub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
    return { priv: kp.privateKey, did: didFromPub(rawPub) };
}

const container = 'https://pod.example/alice/proxion/rooms/room-1/';
const owner = 'https://pod.example/alice/profile/card#me';
const member = 'https://pod.example/bob/profile/card#me';
const date = '2026-01-01T10:00:00.000Z';
const target = id => messageIriAt(container, id, date);
async function fixture() {
    const key = await makeIdentity();
    let actor = owner, epoch = 0, doc = null, version = 0, puts = 0, writeStatus = 0;
    let loseConfirmation = false, unavailableDays = false, interruptRead = false, weak = false;
    const messages = new Map(Array.from({ length: 51 }, (_, i) => ['m' + i, {
        message_id: 'm' + i, iri: target('m' + i), timestamp: date, content: 'Text ' + i,
        from_webid: member, from_display_name: 'Bob', sender_verified: true,
    }]));
    const fetch = vi.fn(async (url, opts = {}) => {
        expect(url).toBe(container + 'pins.json');
        const response = (status, data) => ({ ok: status >= 200 && status < 300, status,
            headers: { get: name => name.toLowerCase() === 'etag' ? (weak ? 'W/' : '') + '"v' + version + '"' : null },
            text: async () => JSON.stringify(data) });
        if (opts.method === 'PUT') {
            puts++;
            if (writeStatus) return response(writeStatus);
            if (doc ? opts.headers['If-Match'] !== '"v' + version + '"' : opts.headers['If-None-Match'] !== '*') return response(412);
            doc = JSON.parse(opts.body); version++;
            return response(200);
        }
        if (interruptRead) epoch++;
        if (loseConfirmation && puts) return response(503);
        return doc ? response(200, doc) : response(404);
    });
    const readDay = vi.fn(async () => ({ ok: !unavailableDays, status: unavailableDays ? 503 : 200,
        messages: [...messages.values()].map(m => ({ ...m })) }));
    const options = { fetch, allowed: url => url === container, getWebId: () => actor,
        getSessionId: () => actor + ':' + epoch, getSigner: () => ({ privKey: key.priv, signerDid: key.did }),
        fetchPeerSigner: async () => ({ signer: key.did }), peerPodRoot: webid => webid === owner ? 'https://pod.example/alice/' : null,
        readDay, retryDelay: vi.fn(async () => {}) };
    const io = createPodPins(options);
    return { io, options, fetch, readDay, messages, puts: () => puts, snapshot: () => doc,
        actor: value => actor = value, stop: () => epoch++, interrupt: () => interruptRead = true,
        failWrites: status => writeStatus = status, loseConfirmation: value => loseConfirmation = value,
        daysUnavailable: value => unavailableDays = value, weak: () => weak = true,
        replace: async pins => { doc = { v: 1, index: container + 'pins.json', owner,
            revision: crypto.randomUUID(), updated: new Date().toISOString(), pins };
            doc.proof = await signRoomPins(doc, key.priv, key.did); version++; },
        pin: (id, add = true, expected) => io.setPin(container, id, date, add, owner, expected),
        list: () => io.readPins(container, owner),
    };
}

describe('durable signed room pins', () => {
    it.each([502, 503, 504])('recovers a transient %s when discovering pins and jumping to an old day', async status => {
        const h = await fixture(); await h.pin('m1');
        h.fetch.mockClear(); h.readDay.mockClear();
        h.fetch.mockResolvedValueOnce({ ok: false, status });
        expect((await h.list()).pins[0].message_id).toBe('m1');
        expect(h.fetch).toHaveBeenCalledTimes(2);
        h.readDay.mockClear(); h.readDay.mockResolvedValueOnce({ ok: false, status, messages: [] });
        expect((await h.io.readMessage(container, target('m1'), owner)).message.message_id).toBe('m1');
        expect(h.readDay).toHaveBeenCalledTimes(2);
        expect(h.options.retryDelay).toHaveBeenCalledTimes(2);
    });
    it('bounds read retries and cancels them if the account changes during the delay', async () => {
        const h = await fixture();
        h.readDay.mockResolvedValue({ ok: false, status: 502, messages: [] });
        expect(await h.io.readMessage(container, target('m1'), owner)).toMatchObject({ ok: false, status: 502 });
        expect(h.readDay).toHaveBeenCalledTimes(2);
        h.readDay.mockClear(); h.options.retryDelay.mockImplementationOnce(async () => h.stop());
        expect(await h.io.readMessage(container, target('m1'), owner)).toMatchObject({ ok: false, reason: 'account_changed' });
        expect(h.readDay).toHaveBeenCalledOnce();
    });
    it.each([401, 403, 404, 429])('does not retry a %s pin read', async status => {
        const h = await fixture();
        h.readDay.mockResolvedValue({ ok: false, status, messages: [] });
        expect(await h.io.readMessage(container, target('m1'), owner)).toMatchObject({ ok: false, status });
        expect(h.readDay).toHaveBeenCalledOnce();
        expect(h.options.retryDelay).not.toHaveBeenCalled();
    });
    it('restores old pins from original day files and reflects edits and withdrawal', async () => {
        const h = await fixture();
        expect(await h.pin('m1')).toMatchObject({ ok: true });
        h.actor(member);
        expect((await h.list()).pins[0]).toMatchObject({ message_id: 'm1', content: 'Text 1', pinned_by: owner });
        expect(h.readDay).toHaveBeenCalledWith(container, '2026-01-01T00:00:00.000Z', '', owner);
        h.messages.get('m1').content = 'Edited';
        expect((await h.list()).pins[0].content).toBe('Edited');
        h.messages.get('m1').deleted = true;
        expect((await h.list()).pins).toEqual([]);
    });
    it('refuses member mutations and preserves valid owner pins on tampering', async () => {
        const h = await fixture(); await h.pin('m1');
        const wrongKey = await makeIdentity();
        const untrusted = createPodPins({ ...h.options, fetchPeerSigner: async () => ({ signer: wrongKey.did }) });
        expect(await untrusted.readPins(container, owner)).toMatchObject({ ok: false, reason: 'invalid_index' });
        h.actor(member);
        expect(await h.pin('m2')).toMatchObject({ ok: false, reason: 'forbidden' });
        expect(h.puts()).toBe(1);
        h.snapshot().pins[0].target = target('m2');
        expect(await h.list()).toMatchObject({ ok: false, reason: 'invalid_index' });
        h.actor(owner);
        expect(await h.pin('m3')).toMatchObject({ ok: false, reason: 'invalid_index' });
        expect(h.puts()).toBe(1);
    });
    it('uses compare-and-swap to preserve concurrent additions from two owner devices', async () => {
        const h = await fixture(), other = createPodPins(h.options);
        const results = await Promise.all([h.pin('m1'), other.setPin(container, 'm2', date, true, owner)]);
        expect(results.every(r => r.ok)).toBe(true);
        expect((await h.list()).pins.map(p => p.message_id).sort()).toEqual(['m1', 'm2']);
    });
    it('keeps rejected writes invisible and acknowledges lost-response retries without duplicate writes', async () => {
        const h = await fixture(); h.failWrites(503);
        expect(await h.pin('m1')).toMatchObject({ ok: false });
        expect((await h.list()).pins).toEqual([]);
        const lost = await fixture(); lost.loseConfirmation(true);
        expect(await lost.pin('m1')).toMatchObject({ ok: false, reason: 'confirmation_failed' });
        lost.loseConfirmation(false);
        expect(await lost.pin('m1')).toMatchObject({ ok: true });
        expect(lost.puts()).toBe(1);
    });
    it('detects stale unpin actions after another device re-pins the message', async () => {
        const h = await fixture(); await h.pin('m1');
        const old = (await h.list()).pins[0].pin_id;
        expect(await h.pin('m1', false, old)).toMatchObject({ ok: true });
        await h.pin('m1');
        expect(await h.pin('m1', false, old)).toMatchObject({ ok: false, reason: 'conflict' });
        expect((await h.list()).pins).toHaveLength(1);
    });
    it('does not turn day read failures into an empty panel and can still unpin a missing target', async () => {
        const h = await fixture(); await h.pin('m1'); h.daysUnavailable(true);
        expect(await h.list()).toMatchObject({ ok: false, status: 503 });
        expect(await h.pin('m1', false)).toMatchObject({ ok: true });
        expect((await h.list()).pins).toEqual([]);
    });
    it('deduplicates pinned-day reads and bounds stale-write retries', async () => {
        const h = await fixture(); await h.pin('m1'); await h.pin('m2'); h.readDay.mockClear();
        expect((await h.list()).pins).toHaveLength(2);
        expect(h.readDay).toHaveBeenCalledOnce();
        h.failWrites(412);
        const before = h.puts();
        expect(await h.pin('m3')).toMatchObject({ ok: false, reason: 'conflict' });
        expect(h.puts() - before).toBe(4);
    });
    it('requires strong versions and cancels reads/queued changes on a session change', async () => {
        const h = await fixture(); await h.pin('m1'); h.weak();
        expect(await h.pin('m2')).toMatchObject({ ok: false, reason: 'version_unavailable' });
        expect(h.puts()).toBe(1);
        const stale = await fixture(); stale.interrupt();
        expect(await stale.pin('m1')).toMatchObject({ ok: false, reason: 'account_changed' });
        expect(stale.puts()).toBe(0);
    });
    it('reclaims withdrawn slots before enforcing the 50-pin limit', async () => {
        const h = await fixture();
        await h.replace(Array.from({ length: MAX_ROOM_PINS }, (_, i) => ({ target: target('m' + i), created: date, id: 'pin-' + i })));
        expect(await h.pin('m50')).toMatchObject({ ok: false, reason: 'pin_limit' });
        h.messages.get('m1').deleted = true;
        expect(await h.pin('m50')).toMatchObject({ ok: true });
        expect(h.snapshot().pins).toHaveLength(MAX_ROOM_PINS);
        expect(h.snapshot().pins.some(p => p.target === target('m1'))).toBe(false);
    });
    it('rejects off-room and malformed pin targets before fetching a day', async () => {
        const h = await fixture();
        for (const bad of ['https://evil.example/chat.ttl#m1', container + '../chat.ttl#m1', container + '2026/02/30/chat.ttl#m1', container + '2026/01/01/chat.ttl#a%2fb']) {
            expect(pinTarget(container, bad)).toBeNull();
            expect(await h.io.readMessage(container, bad, owner)).toMatchObject({ ok: false });
        }
        expect(h.readDay).not.toHaveBeenCalled();
        expect(normalizePinSnapshot({ ...h.snapshot(), v: 9 }, container, owner)).toBeNull();
    });
});
