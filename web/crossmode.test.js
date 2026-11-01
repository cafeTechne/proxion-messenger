// crossmode.js: hybrid routing of desktop DMs to WebID contacts, and the
// identity this device publishes when the pod bridge comes up.
import { describe, it, expect, vi } from 'vitest';
import { createCrossMode, webIdTarget, callWebIdTarget } from './crossmode.js';
import { UNRESOLVED_SIGNER } from './webcalls.js';

const ME = 'https://pod.example/me/profile/card#me';
const ROOT = 'https://pod.example/me/';
const BOB = 'https://pod.example/bob/profile/card#me';

// A fake gateway: answers pod_fetch from a route table, records everything.
function harness({ routes = {}, prevSigner = null, known = () => true } = {}) {
    const sent = [], events = [];
    let cm = null;
    const answer = (m) => {
        if (m.cmd !== 'pod_fetch') return;
        let r = routes[`${m.method} ${m.url}`];
        if (m.method === 'GET' && m.url === ROOT + 'proxion/identity/signer.json' && prevSigner) {
            r = { status: 200, body: JSON.stringify({ version: 1, signer: prevSigner, account_did: null }) };
        }
        r = r || (m.method === 'GET' ? { status: 404 } : { status: 201 });
        setTimeout(() => cm.handleEvent({ type: 'pod_fetch_result', req_id: m.req_id, headers: {}, body: '', ...r }), 0);
    };
    cm = createCrossMode({
        send: (m) => { sent.push(m); answer(m); },
        handleEvent: (ev) => events.push(ev),
        getClientDid: () => 'did:key:zDevice',
        getDisplayName: () => 'Me',
        signEnvelope: async () => ({ signer: 'did:key:zDevice', sig: 'S' }),
        verifyDmSig: async () => true, verifyFanoutSig: async () => true, verifyDeviceCert: async () => null,
        e2e: { cachePeerPub: vi.fn(), ratchetDecrypt: vi.fn(), hasSession: async () => false },
        isBlocked: () => false, isKnownSender: (w) => known(w), onMessageRequest: vi.fn(),
        getContacts: () => [BOB, 'did:key:zGatewayContact'],
        getMyPub: () => 'PUB',
        onStateChange: () => {},
    });
    return { cm, sent, events, setRoute: (k, v) => { routes[k] = v; } };
}

const settle = (ms = 30) => new Promise((r) => setTimeout(r, ms));

async function ready(h) {
    h.cm.handleEvent({ type: 'pod_bridge', ready: true, webid: ME, pod_root: ROOT, consumer: true });
    for (let i = 0; i < 100 && !h.sent.some((m) => m.cmd === "pod_watch" && m.url.endsWith("presence.json")); i++) await settle(10);
}

describe('webIdTarget', () => {
    it('routes only DM commands addressed to a WebID', () => {
        expect(webIdTarget({ cmd: 'local_dm', target_webid: BOB })).toBe(BOB);
        expect(webIdTarget({ cmd: 'local_dm', target_webid: 'did:key:zX' })).toBe(null);
        expect(webIdTarget({ cmd: 'send_room', target_webid: BOB })).toBe(null);
        expect(webIdTarget({ cmd: 'local_dm', target_webid: 'https://gw.example/i/abc' })).toBe(null);
    });
});

describe('createCrossMode', () => {
    it('leaves gateway traffic alone', () => {
        const { cm } = harness();
        expect(cm.route({ cmd: 'local_dm', target_webid: 'did:key:zPeer', content: 'x' })).toBe(false);
        expect(cm.route({ cmd: 'send_room', room_id: 'r', content: 'x' })).toBe(false);
        expect(cm.route({ cmd: 'send_dm_fanout', fanout: [{ to_webid: 'did:key:zP', payload: {} }] })).toBe(false);
    });

    it('holds a WebID DM while the bridge is not ready (stays pending, never to the gateway)', () => {
        const { cm, sent } = harness();
        expect(cm.route({ cmd: 'local_dm', target_webid: BOB, message_id: 'm1', content: 'CT', e2e: true })).toBe(true);
        expect(sent).toEqual([]);
    });

    it('publishes this device as the signer with no account did, and the X25519 key', async () => {
        const h = harness();
        await ready(h);
        const signerPut = h.sent.find((m) => m.method === 'PUT' && m.url === ROOT + 'proxion/identity/signer.json');
        expect(JSON.parse(signerPut.body)).toEqual({ version: 1, signer: 'did:key:zDevice', account_did: null });
        const pubPut = h.sent.find((m) => m.method === 'PUT' && m.url === ROOT + 'proxion/identity/x25519-pub.json');
        expect(JSON.parse(pubPut.body)).toEqual({ version: 1, pub: 'PUB' });
        expect(h.cm.state().conflict).toBe(false);
        // Presence is watched for WebID contacts only.
        const watched = h.sent.filter((m) => m.cmd === 'pod_watch').map((m) => m.url);
        expect(watched).toContain('https://pod.example/bob/proxion/presence.json');
        expect(watched).toContain(ROOT + 'proxion/dm-inbox/');
    });

    it('flags taking over from another device', async () => {
        const h = harness({ prevSigner: 'did:key:zOtherDevice' });
        await ready(h);
        expect(h.cm.state().conflict).toBe(true);
    });

    it('drops a WebID DM into the peer pod, signed, and confirms only on success', async () => {
        const h = harness({ routes: { 'POST https://pod.example/bob/proxion/dm-inbox/': { status: 201 } } });
        await ready(h);
        h.cm.route({ cmd: 'local_dm', target_webid: BOB, thread_id: BOB, message_id: 'm1', content: 'CT', e2e: true, nonce: 'n' });
        await settle();
        const post = h.sent.find((m) => m.method === 'POST' && m.url === 'https://pod.example/bob/proxion/dm-inbox/');
        const env = JSON.parse(post.body);
        expect(env).toMatchObject({ from_webid: ME, content: 'CT', e2e: true, signer: 'did:key:zDevice', sig: 'S' });
        expect(h.events.find((e) => e._podEcho)).toMatchObject({ message_id: 'm1', thread_id: BOB });

        const f = harness({ routes: { 'POST https://pod.example/bob/proxion/dm-inbox/': { status: 403 } } });
        await ready(f);
        f.cm.route({ cmd: 'local_dm', target_webid: BOB, message_id: 'm2', content: 'CT', e2e: true });
        await settle();
        expect(f.events.some((e) => e._podEcho)).toBe(false);
    });

    it('swallows self-sync fanout for a WebID thread', () => {
        const { cm } = harness();
        expect(cm.route({ cmd: 'send_dm_fanout', fanout: [{ to_webid: 'did:key:zMe', payload: { thread_peer: BOB } }] })).toBe(true);
    });
});

describe('callWebIdTarget', () => {
    it('routes only call signals addressed to a WebID', () => {
        for (const cmd of ['voice_invite', 'voice_answer', 'ice_candidate', 'voice_hangup']) {
            expect(callWebIdTarget({ cmd, target_webid: BOB })).toBe(BOB);
            expect(callWebIdTarget({ cmd, target_webid: 'did:key:zPeer' })).toBe(null);
        }
        expect(callWebIdTarget({ cmd: 'join_voice_channel', target_webid: BOB })).toBe(null);
        expect(callWebIdTarget({ cmd: 'local_dm', target_webid: BOB })).toBe(null);
    });
});

describe('createCrossMode calls', () => {
    const CALL_BOX = 'https://pod.example/bob/proxion/call-inbox/';

    it('leaves gateway calls alone', () => {
        const { cm } = harness();
        expect(cm.route({ cmd: 'voice_invite', target_webid: 'did:key:zPeer', sdp_offer: 'O' })).toBe(false);
        expect(cm.route({ cmd: 'voice_hangup', session_id: 's' })).toBe(false);
    });

    it('drops a signal for a WebID contact into their call-inbox, without the gateway cert', async () => {
        const h = harness({ routes: { ['POST ' + CALL_BOX]: { status: 201 } } });
        await ready(h);
        const handled = h.cm.route({
            cmd: 'voice_invite', target_webid: BOB, session_id: 's1', sdp_offer: 'OFFER',
            fp_sig: 'SIG', fp_signer: 'did:key:zDevice', fp_cert: { device_did: 'did:key:zDevice', account_did: 'did:key:zGateway' },
        });
        expect(handled).toBe(true);
        await settle();
        const post = h.sent.find((m) => m.method === 'POST' && m.url === CALL_BOX);
        const sig = JSON.parse(post.body);
        expect(sig).toMatchObject({ type: 'voice_invite', from_webid: ME, caller_webid: ME, session_id: 's1', sdp_offer: 'OFFER', fp_sig: 'SIG', fp_signer: 'did:key:zDevice' });
        expect(sig.fp_cert).toBeUndefined();
        expect(sig.target_webid).toBeUndefined();
        expect(h.sent.some((m) => m.cmd === 'voice_invite')).toBe(false);
        expect(h.events.some((e) => e.type === 'voice_unavailable')).toBe(false);
    });

    it('ends a call it cannot place instead of ringing out', async () => {
        const off = harness();
        expect(off.cm.route({ cmd: 'voice_invite', target_webid: BOB, session_id: 's0' })).toBe(true);
        expect(off.sent).toEqual([]);
        await settle();
        expect(off.events).toContainEqual(expect.objectContaining({ type: 'voice_unavailable', session_id: 's0' }));

        const h = harness({ routes: { ['POST ' + CALL_BOX]: { status: 404 } } });
        await ready(h);
        h.cm.route({ cmd: 'voice_invite', target_webid: BOB, session_id: 's2' });
        await settle();
        expect(h.events).toContainEqual(expect.objectContaining({ type: 'voice_unavailable', session_id: 's2' }));
    });

    it('polls its own call-inbox fast while a call is up', async () => {
        const h = harness();
        await ready(h);
        for (let i = 0; i < 50 && !h.sent.some((m) => m.cmd === 'pod_watch' && m.url === ROOT + 'proxion/call-inbox/'); i++) await settle(10);
        const watches = () => h.sent.filter((m) => m.cmd === 'pod_watch' && m.url === ROOT + 'proxion/call-inbox/');
        expect(watches().map((m) => m.fast)).toEqual([false]);
        h.cm.setCallActive(true);
        h.cm.setCallActive(false);
        expect(watches().map((m) => m.fast)).toEqual([false, true, false]);
    });

    it('rings only for contacts', async () => {
        const h = harness({ known: (w) => w === BOB });
        await ready(h);
        for (let i = 0; i < 50 && !h.sent.some((m) => m.cmd === 'pod_watch' && m.url === ROOT + 'proxion/call-inbox/'); i++) await settle(10);
        const sig = (from, id) => ({ type: 'voice_invite', from_webid: from, caller_webid: from, session_id: id, sdp_offer: 'O' });
        const listing = { '@id': ROOT + 'proxion/call-inbox/', contains: [{ '@id': ROOT + 'proxion/call-inbox/a' }, { '@id': ROOT + 'proxion/call-inbox/b' }] };
        h.setRoute('GET ' + ROOT + 'proxion/call-inbox/', { status: 200, body: JSON.stringify(listing) });
        h.setRoute('GET ' + ROOT + 'proxion/call-inbox/a', { status: 200, body: JSON.stringify(sig('https://pod.example/eve/profile/card#me', 'evil')) });
        h.setRoute('GET ' + ROOT + 'proxion/call-inbox/b', { status: 200, body: JSON.stringify(sig(BOB, 'good')) });
        await h.cm.drainCalls();
        expect(h.events.filter((e) => e.type === 'voice_invite').map((e) => e.session_id)).toEqual(['good']);
        expect(h.sent.filter((m) => m.method === 'DELETE').map((m) => m.url).sort())
            .toEqual([ROOT + 'proxion/call-inbox/a', ROOT + 'proxion/call-inbox/b']);
    });

    it('expects the signer the contact publishes', async () => {
        const h = harness({ routes: { 'GET https://pod.example/bob/proxion/identity/signer.json': {
            status: 200, body: JSON.stringify({ version: 1, signer: 'did:key:zBobDevice', account_did: null }) } } });
        await ready(h);
        expect(await h.cm.expectedCallSigner(null, { caller_webid: BOB })).toBe('did:key:zBobDevice');
        expect(await h.cm.expectedCallSigner({ peerWebid: 'did:key:zPeer' }, {})).toBe('');
        const n = harness();
        await ready(n);
        expect(await n.cm.expectedCallSigner(null, { caller_webid: BOB })).toBe(UNRESOLVED_SIGNER + BOB);
    });
});
