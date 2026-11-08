import { describe, it, expect, vi } from 'vitest';
import { createFriendIo, friendIntentUrl, friendshipState } from './friendio.js';
import { createWebFriends } from './webfriends.js';
import { signFriendIntent } from './dmsig.js';

async function identity() {
    const key = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
    const pub = new Uint8Array(await crypto.subtle.exportKey('raw', key.publicKey));
    const bytes = new Uint8Array([0xed, 1, ...pub]);
    const digits = [0], alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
    for (const byte of bytes) {
        let carry = byte;
        for (let i = 0; i < digits.length; i++) { carry += digits[i] << 8; digits[i] = carry % 58; carry = Math.floor(carry / 58); }
        while (carry) { digits.push(carry % 58); carry = Math.floor(carry / 58); }
    }
    return { privKey: key.privateKey, signerDid: 'did:key:z' + digits.reverse().map(i => alphabet[i]).join('') };
}
function store() {
    const files = new Map(), inboxes = new Map(), keys = new Map(), calls = [];
    let sequence = 0;
    const reply = (status, body = '', etag = null) => ({ status, ok: status >= 200 && status < 300,
        headers: new Headers(etag ? { etag } : {}), text: async () => body });
    const state = { files, inboxes, keys, calls, reply, hook: null, notifyFails: false,
        set(url, doc) { files.set(url, { body: typeof doc === 'string' ? doc : JSON.stringify(doc), etag: '"v' + (++sequence) + '"' }); },
        async fetch(url, options = {}) {
            const method = options.method || 'GET'; calls.push({ url, ...options, method });
            if (state.hook) { const result = await state.hook(url, options); if (result) return result; }
            const entry = files.get(url);
            if (method === 'GET' || method === 'HEAD') return entry ? reply(200, entry.body, entry.etag) : reply(404);
            if (method === 'PUT') {
                if (options.headers?.['If-None-Match'] === '*' && entry) return reply(412);
                if (options.headers?.['If-Match'] && options.headers['If-Match'] !== entry?.etag) return reply(412);
                state.set(url, options.body); return reply(entry ? 205 : 201);
            }
            return reply(400);
        } };
    return state;
}
async function client(root, data, { blocked = () => false } = {}) {
    const signer = await identity(), webId = root + 'profile/card#me';
    data.keys.set(root, signer.signerDid);
    const session = { root, webId, id: crypto.randomUUID(), ready: true };
    const io = createFriendIo({ fetch: data.fetch, getSession: () => session, getSigner: () => signer });
    const pod = {
        podEnsureFriendInbox: async () => { if (!data.inboxes.has(root)) data.inboxes.set(root, []); return root + 'proxion/friend-inbox/'; },
        podReadFriendDrops: async () => [...(data.inboxes.get(root) || [])],
        podDropFriend: async (to, snapshot) => {
            if (data.notifyFails || !data.inboxes.has(to)) return false;
            data.inboxes.get(to).push({ url: to + 'proxion/friend-inbox/' + crypto.randomUUID(), snapshot: structuredClone(snapshot) }); return true;
        },
        podDeleteFriendDrop: async url => { data.inboxes.set(root, data.inboxes.get(root).filter(drop => drop.url !== url)); return true; },
        podFetchPeerSigner: async peerRoot => data.keys.has(peerRoot) ? { signer: data.keys.get(peerRoot) } : null,
    };
    const onChange = vi.fn();
    const engine = createWebFriends({ io, pod, isBlocked: blocked, onChange, setTimer: () => 1, clearTimer: () => {} });
    await engine.start();
    return { io, engine, pod, session, signer, webId, root, onChange };
}
async function pair() {
    const data = store(), a = await client('https://pod.example/alice/', data), b = await client('https://pod.example/bob/', data);
    return { data, a, b };
}
async function request(a, b) {
    expect((await a.engine.request(b.webId)).ok).toBe(true);
    await b.engine.refresh();
    expect(b.engine.stateFor(a.webId).state).toBe('incoming');
    return b.engine.stateFor(a.webId).theirs.offer.id;
}
async function befriend(a, b) {
    const id = await request(a, b);
    expect((await b.engine.accept(a.webId, id)).ok).toBe(true);
    await a.engine.refresh();
    expect(a.engine.stateFor(b.webId).state).toBe('friends');
    expect(b.engine.stateFor(a.webId).state).toBe('friends');
    return id;
}

describe('browser mutual friendships', () => {
    it('requests and accepts explicitly, restoring both lists on another device/key', async () => {
        const { data, a, b } = await pair();
        const id = await befriend(a, b);
        a.engine.stop(); b.engine.stop();
        const nextA = await client(a.root, data), nextB = await client(b.root, data);
        expect(nextA.engine.stateFor(b.webId).state).toBe('friends');
        expect(nextB.engine.stateFor(a.webId).state).toBe('friends');
        expect(nextB.engine.stateFor(a.webId).mine.decision.id).toBe(id);
    });
    it('withdraws a request and rejects a stale accept without recreating friendship', async () => {
        const { a, b } = await pair(); const id = await request(a, b);
        expect((await a.engine.withdraw(b.webId, id)).ok).toBe(true);
        expect(await b.engine.accept(a.webId, id)).toMatchObject({ ok: false, reason: 'request_changed' });
        await b.engine.refresh(); expect(b.engine.stateFor(a.webId).state).toBe('none');
    });
    it('declines, closes the old offer, and binds a subsequent request to a fresh ID', async () => {
        const { a, b } = await pair(); const first = await request(a, b);
        expect((await b.engine.decline(a.webId, first)).ok).toBe(true);
        await a.engine.refresh(); expect(a.engine.stateFor(b.webId).state).toBe('none');
        const second = await request(a, b); expect(second).not.toBe(first);
        expect(await b.engine.accept(a.webId, first)).toMatchObject({ ok: false, reason: 'request_changed' });
        expect((await b.engine.accept(a.webId, second)).ok).toBe(true);
    });
    it('removes both crossed-request consents so old decisions cannot resurrect a friend', async () => {
        const { a, b } = await pair(); const first = await request(a, b);
        expect((await b.engine.request(a.webId)).ok).toBe(true);
        await a.engine.refresh(); expect((await b.engine.accept(a.webId, first)).ok).toBe(true);
        await a.engine.refresh(); expect(a.engine.stateFor(b.webId).state).toBe('friends');
        expect((await a.engine.remove(b.webId)).ok).toBe(true);
        await b.engine.refresh(); await a.engine.refresh();
        expect(a.engine.stateFor(b.webId).state).toBe('none'); expect(b.engine.stateFor(a.webId).state).toBe('none');
        expect(await b.engine.accept(a.webId, first)).toMatchObject({ ok: false, reason: 'request_changed' });
    });
    it('retries undelivered requests from durable resources after restart', async () => {
        const { data, a, b } = await pair(); data.notifyFails = true;
        expect((await a.engine.request(b.webId)).ok).toBe(true);
        expect(a.engine.stateFor(b.webId).queued).toBe(true); a.engine.stop();
        data.notifyFails = false; const restored = await client(a.root, data);
        expect(restored.engine.stateFor(b.webId).queued).toBe(false);
        await b.engine.refresh(); expect(b.engine.stateFor(a.webId).state).toBe('incoming');
    });
    it('never acknowledges a rejected intent write and retries without duplicate offers', async () => {
        const { data, a, b } = await pair(); const url = await friendIntentUrl(a.root, b.webId);
        data.hook = (target, options) => target === url && options.method === 'PUT' ? data.reply(503) : null;
        expect((await a.engine.request(b.webId)).ok).toBe(false);
        expect(a.engine.stateFor(b.webId).state).toBe('none');
        data.hook = null; await request(a, b);
        const id = a.engine.stateFor(b.webId).mine.offer.id;
        await a.engine.request(b.webId); expect(a.engine.stateFor(b.webId).mine.offer.id).toBe(id);
    });
    it('preserves concurrent index additions with conditional retries', async () => {
        const { data, a, b } = await pair();
        const c = 'https://pod.example/carol/profile/card#me';
        expect((await Promise.all([a.io.remember(b.webId), a.io.remember(c)])).every(result => result.ok)).toBe(true);
        expect((await a.io.readIndex()).entries.map(entry => entry.peer).sort()).toEqual([b.webId, c].sort());
        expect(data.calls.filter(call => call.method === 'PUT' && call.url.endsWith('index.json')).every(call => call.headers['If-Match'] || call.headers['If-None-Match'])).toBe(true);
    });
    it('does not overwrite a newer request from a stale withdrawal button', async () => {
        const { a, b } = await pair(); const id = await request(a, b);
        await a.engine.withdraw(b.webId, id); await b.engine.refresh(); await request(a, b);
        expect(await a.engine.withdraw(b.webId, id)).toMatchObject({ ok: false, reason: 'request_changed' });
        expect(a.engine.stateFor(b.webId).state).toBe('outgoing');
    });
    it('refuses unsafe/self/blocked peers before making authenticated peer requests', async () => {
        const { data, a } = await pair(); const count = data.calls.length;
        expect((await a.engine.request(a.webId)).ok).toBe(false);
        expect((await a.engine.request('http://127.0.0.1/profile/card#me')).ok).toBe(false);
        expect(data.calls).toHaveLength(count);
        const blocked = await client('https://pod.example/carol/', data, { blocked: () => true });
        expect(await blocked.engine.request(a.webId)).toMatchObject({ ok: false, reason: 'blocked' });
    });
    it('does not trust a forged inbox claim or a tampered canonical intent', async () => {
        const { data, a, b } = await pair(); const evil = await identity();
        const snapshot = { v: 1, actor: a.webId, peer: b.webId, revision: crypto.randomUUID(), updated: new Date().toISOString(), offer: { id: 'attack', state: 'pending' }, decision: null };
        snapshot.proof = await signFriendIntent(snapshot, evil.privKey, evil.signerDid);
        data.inboxes.get(b.root).push({ url: b.root + 'proxion/friend-inbox/forged', snapshot });
        await b.engine.refresh(); expect(b.engine.stateFor(a.webId).state).toBe('none');
        await a.engine.request(b.webId);
        const url = await friendIntentUrl(a.root, b.webId); const doc = JSON.parse(data.files.get(url).body);
        doc.offer.id = 'tampered'; data.set(url, doc);
        expect(await b.io.read(a.webId)).toMatchObject({ ok: false, reason: 'invalid' });
    });
    it('fails closed on absent validators and cancels an old account write', async () => {
        const { data, a, b } = await pair(); await request(a, b);
        const index = a.root + 'proxion/friends/index.json'; data.files.get(index).etag = 'W/"weak"';
        expect(await a.io.remember('https://pod.example/carol/profile/card#me')).toMatchObject({ ok: false, reason: 'validator_missing' });
        data.files.get(index).etag = '"strong"';
        let release; data.hook = (url, options) => url === index && !options.method ? new Promise(resolve => { release = () => resolve(null); }) : null;
        const pending = a.engine.request('https://pod.example/carol/profile/card#me');
        await vi.waitFor(() => expect(release).toBeTypeOf('function'));
        a.session.id = 'new-session'; release();
        expect(await pending).toMatchObject({ ok: false });
        expect(data.calls.some(call => call.method === 'PUT' && call.url.includes('undefined'))).toBe(false);
    });
});

it('confirms a withdrawal that lands while an acceptance write is in flight', async () => {
    const { data, a, b } = await pair(); const id = await request(a, b);
    const url = await friendIntentUrl(b.root, a.webId);
    let resume;
    data.hook = (target, options) => target === url && options.method === 'PUT'
        ? new Promise(resolve => { resume = () => resolve(null); }) : null;
    const accepting = b.engine.accept(a.webId, id);
    await vi.waitFor(() => expect(resume).toBeTypeOf('function'));
    expect((await a.engine.withdraw(b.webId, id)).ok).toBe(true);
    const release = resume; data.hook = null; release();
    expect(await accepting).toMatchObject({ ok: false, reason: 'request_changed' });
    await b.engine.refresh(); await a.engine.refresh();
    expect(a.engine.stateFor(b.webId).state).toBe('none'); expect(b.engine.stateFor(a.webId).state).toBe('none');
});
