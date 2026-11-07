import { describe, it, expect } from 'vitest';
import { signLongChatChange } from './dmsig.js';
import { applyMessageChanges, parseMessageChanges, changeOps, CHANGE_PAYLOAD } from './room-changes.js';
import { createPodRoomIo } from './podroomio.js';
import { NS, parseLongChatJsonLd, messageIriAt } from './longchat.js';
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
const root = 'https://pod.example/alice/';
const owner = root + 'profile/card#me';
const author = 'https://pod.example/bob/profile/card#me';
const stranger = 'https://pod.example/eve/profile/card#me';
const container = root + 'proxion/rooms/test-room/';
const timestamp = '2026-11-07T12:00:00.000Z';
const target = messageIriAt(container, 'm1', timestamp);
const original = () => ({ iri: target, message_id: 'm1', from_webid: author, content: 'Original', timestamp });
const node = c => ({ '@id': c.id, [CHANGE_PAYLOAD]: [{ '@value': JSON.stringify(c) }] });
async function fixture() {
    const keys = new Map(await Promise.all([owner, author, stranger].map(async actor => [actor, await makeIdentity()])));
    const trust = { ownerWebId: owner, peerPodRoot: actor => actor,
        fetchPeerSigner: async actor => ({ signer: keys.get(actor)?.did }) };
    async function signed(overrides = {}) {
        const c = { id: target.split('#')[0] + '#change-a', target, actor: author,
            kind: 'edit', content: 'Edited', created: timestamp, previous: '', ...overrides };
        c.proof = await signLongChatChange(c, keys.get(c.actor).priv, keys.get(c.actor).did);
        return c;
    }
    async function apply(changes, message = original()) { return (await applyMessageChanges([message], changes.map(node), trust))[0]; }
    return { keys, trust, signed, apply };
}

describe('signed append-only room changes', () => {
    it('edits without replacing the original Long Chat graph', async () => {
        const f = await fixture(), c = await f.signed();
        expect(await f.apply([c])).toMatchObject({ content: 'Edited', revision_id: c.id, edited_at: timestamp });
        expect(parseLongChatJsonLd([node(c)])).toEqual([]);
        expect(changeOps(c).deletes).toBeUndefined();
    });
    it('authenticates every field and the published key', async () => {
        const f = await fixture(), c = await f.signed();
        for (const field of ['content', 'target', 'actor', 'created', 'previous', 'kind', 'id']) {
            const altered = { ...c, [field]: field === 'actor' ? owner : field === 'kind' ? 'delete' : c[field] + 'x' };
            expect((await f.apply([altered])).content).toBe('Original');
        }
        expect((await f.apply([{ ...c, proof: '' }])).content).toBe('Original');
        f.trust.fetchPeerSigner = async () => ({ signer: f.keys.get(stranger).did });
        expect((await f.apply([c])).content).toBe('Original');
    });
    it('permits author changes and owner withdrawal while refusing other writers', async () => {
        const f = await fixture();
        for (const actor of [owner, stranger]) expect((await f.apply([await f.signed({ actor })])).content).toBe('Original');
        expect((await f.apply([await f.signed({ actor: stranger, kind: 'delete', content: '' })])).deleted).not.toBe(true);
        for (const actor of [owner, author]) expect((await f.apply([await f.signed({ actor, kind: 'delete', content: '' })])).deleted).toBe(true);
    });
    it('keeps withdrawals permanent and resolves concurrent edits consistently', async () => {
        const f = await fixture(), a = await f.signed(), b = await f.signed({ id: a.id + 'b', content: 'Second' });
        expect((await f.apply([a,b])).content).toBe('Second');
        expect((await f.apply([b,a])).content).toBe('Second');
        const deleted = await f.signed({ kind: 'delete', content: '' });
        expect(await f.apply([deleted,b])).toMatchObject({ deleted: true, content: '' });
        expect(await f.apply([b,deleted])).toMatchObject({ deleted: true, content: '' });
    });
    it('ignores malformed and oversized payloads without masking valid records', async () => {
        const f = await fixture(), c = await f.signed();
        const n = node(c); n[CHANGE_PAYLOAD].unshift({ '@value': 'null' }, { '@value': 'x'.repeat(65537) });
        expect(parseMessageChanges([n])).toEqual([c]);
    });
});

async function ioFixture() {
    const f = await fixture();
    let actor = author, live = true, status = 200, writes = 0, failConfirmation = false, stopAtHead = false;
    const graph = [{ '@id': target, [NS.sioc + 'content']: [{ '@value': 'Original' }],
        [NS.foaf + 'maker']: [{ '@id': author }], [NS.dct + 'created']: [{ '@value': timestamp }] }];
    const fetch = async (_url, opts = {}) => {
        if (opts.method === 'HEAD' && stopAtHead) live = false;
        if (!opts.method && failConfirmation && writes) return { ok: false, status: 503 };
        if (opts.method === 'PATCH') {
            writes++;
            if (status >= 400) return { ok: false, status };
            const literal = opts.body.match(/<[^>]*changePayload>\s+("(?:[^"\\]|\\.)*")/);
            if (!literal) throw Error('Missing change payload: ' + opts.body);
            graph.push(node(JSON.parse(JSON.parse(literal[1]))));
        }
        return { ok: true, status: 200, text: async () => JSON.stringify(graph), headers: { get: () => null } };
    };
    const io = createPodRoomIo({ fetch, isLoggedIn: () => true, storageRoot: () => root,
        webId: () => actor, getSigner: () => ({ privKey: f.keys.get(actor).priv, signerDid: f.keys.get(actor).did }),
        fetchPeerSigner: async podRoot => ({ signer: f.keys.get(podRoot + 'profile/card#me')?.did }) });
    return { io, graph, writes: () => writes, setStatus: v => status = v,
        switchActor: v => actor = v, stop: () => live = false,
        breakConfirmation: v => failConfirmation = v, stopDuringHead: () => stopAtHead = true,
        change: change => io.podChangeMessageAt(container, 'm1', timestamp, change, owner, () => live) };
}

describe('confirmed room changes', () => {
    it('persists an edit, confirms reads, and retries without duplicate writes', async () => {
        const h = await ioFixture();
        expect(await h.change({ kind: 'edit', content: 'New text' })).toMatchObject({ ok: true, message: { content: 'New text' } });
        expect(h.graph[0][NS.sioc + 'content'][0]['@value']).toBe('Original');
        expect(await h.change({ kind: 'edit', content: 'New text' })).toMatchObject({ ok: true });
        expect(h.writes()).toBe(1);
        expect(await h.change({ kind: 'edit', content: 'Stale draft' })).toMatchObject({ ok: false, reason: 'conflict' });
        const messages = await h.io.podReadChatDayAt(container, timestamp, 'test-room', owner);
        expect(messages[0].content).toBe('New text');
        expect(await h.change({ kind: 'delete', expectedRevision: messages[0].revision_id })).toMatchObject({ ok: true, message: { deleted: true } });
        expect(await h.change({ kind: 'edit', content: 'Restore' })).toMatchObject({ ok: false, reason: 'deleted' });
    });
    it('retains the message after rejected writes and allows retry', async () => {
        const h = await ioFixture(); h.setStatus(403);
        expect(await h.change({ kind: 'delete' })).toMatchObject({ ok: false, status: 403 });
        expect((await h.io.podReadChatDayAt(container, timestamp))[0].deleted).not.toBe(true);
        h.setStatus(200);
        expect(await h.change({ kind: 'delete' })).toMatchObject({ ok: true });
    });
    it('can recover a saved edit after losing its confirmation read', async () => {
        const h = await ioFixture(); h.breakConfirmation(true);
        expect(await h.change({ kind: 'edit', content: 'Saved but unread' })).toMatchObject({ ok: false, reason: 'confirmation_failed' });
        h.breakConfirmation(false);
        expect(await h.change({ kind: 'edit', content: 'Saved but unread' })).toMatchObject({ ok: true });
        expect(h.writes()).toBe(1);
    });
    it('does not PATCH with a stale session after capability discovery', async () => {
        const h = await ioFixture(); h.stopDuringHead();
        expect(await h.change({ kind: 'edit', content: 'Stale' })).toMatchObject({ ok: false, reason: 'account_changed' });
        expect(h.writes()).toBe(0);
    });
    it('blocks foreign authors and stale sessions, and permits owner moderation', async () => {
        const h = await ioFixture(); h.switchActor(stranger);
        expect(await h.change({ kind: 'delete' })).toMatchObject({ ok: false, reason: 'forbidden' });
        expect(h.writes()).toBe(0);
        h.switchActor(owner);
        expect(await h.change({ kind: 'delete' })).toMatchObject({ ok: true });
        h.stop();
        expect(await h.change({ kind: 'delete' })).toMatchObject({ ok: false });
    });
});
