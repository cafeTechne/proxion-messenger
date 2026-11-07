// Gateway-free pod round-trips against a live CSS pod, fully automated.
//
// This is the automation that replaces the previously-manual "provision a CSS
// pod" step: scripts/css-harness.mjs spins up a throwaway Community Solid Server
// and mints client credentials, and @inrupt/solid-client-authn-node turns those
// into authenticated sessions injected into pod.js (the same vi.mock('./auth.js')
// pattern pod.test.js uses). No Docker, no external pod, no browser-OIDC UI.
//
// It verifies the two things gateway-free operation depends on, end to end:
//   Phase 1 (R102): create a room on the pod and read its messages back.
//   Phase 2 (R103): drop an encrypted DM envelope into a peer's pod and read it.
//
// Gated behind PROXION_LIVE_POD so `npm test` stays fast and offline-safe:
//   PROXION_LIVE_POD=1 npm run test:integration
// (First run downloads @solid/community-server via npx.)

import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';

// Live bindings the auth.js mock reads; we flip them between Alice and Bob.
let _session = null;
let _storageRoot = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import { startCss, provisionAccount, makeAuthSession } from './scripts/css-harness.mjs';
import {
    ensureProxionContainer,
    podWriteRoomDescriptor, podReadRoomDescriptor,
    podWriteMessageJsonLd, podReadLongChatRecent,
    podEnsureDmInbox, podDropDm, podReadDmDrops, podDeleteDmDrop,
    podWritePresence, podReadPresence,
    podEnsureCallInbox, podDropSignal, podReadSignals, podDeleteSignal,
    podEnsureJoinInbox, podDropJoin, podReadJoins, podDeleteJoin,
    podWritePendingJoin, podHasPendingJoin, podDeletePendingJoin,
    podReadJoinedRooms, podWriteJoinedRooms,
    podGrantChatParticipants, podWriteChatMessageAt, podReadChatRecentAt, podDeleteOwnedRoom,
} from './pod.js';
import { buildRoomDescriptor, withMembers } from './roomdesc.js';
import { createPodIo } from './podio.js';
import { createPodRoomIo } from './podroomio.js';
import { createJoinedRoomStore, withJoinedRecord } from './webrooms.js';
import { createOwnerMembership } from './room-membership.js';
import { chatRootUrl } from './longchat.js';
import { createWebDm } from './webdm.js';
import { statusFromHeartbeat } from './webpresence.js';
import { createWebJoin } from './webjoin.js';

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


const LIVE = !!process.env.PROXION_LIVE_POD;
const uid = (p) => `${p}-${Math.random().toString(36).slice(2, 9)}`;

describe.skipIf(!LIVE)('gateway-free pod round-trips (live CSS)', () => {
    let css, alice, bob, aliceSession, bobSession;

    beforeAll(async () => {
        css = await startCss();
        alice = await provisionAccount(css.url, { email: `${uid('alice')}@test.example`, password: 'pw-12345678', label: 'alice' });
        bob = await provisionAccount(css.url, { email: `${uid('bob')}@test.example`, password: 'pw-12345678', label: 'bob' });
        // Hand-rolled DPoP sessions (openid-client does not work under vitest).
        aliceSession = await makeAuthSession(alice);
        bobSession = await makeAuthSession(bob);
    }, 180000);

    afterAll(async () => {
        css?.stop();
    });

    const asAlice = () => { _session = aliceSession; _storageRoot = alice.storageRoot; };
    const asBob = () => { _session = bobSession; _storageRoot = bob.storageRoot; };

    it('Phase 1: writes a room + message to the pod and reads them back (no gateway)', async () => {
        asAlice();
        await ensureProxionContainer();
        const roomId = uid('room');
        const desc = buildRoomDescriptor({
            roomId, title: 'Integration Room', owner: alice.webId,
            members: [{ webid: alice.webId, role: 'owner' }],
        });
        expect(await podWriteRoomDescriptor({ ...desc, name: 'Integration Room', creator_webid: alice.webId })).toBe(true);

        const back = await podReadRoomDescriptor(roomId);
        expect(back?.room_id).toBe(roomId);
        expect(back?.owner).toBe(alice.webId);

        const mid = uid('m');
        await podWriteMessageJsonLd(roomId, mid, {
            content: 'hello from the pod', from_webid: alice.webId, timestamp: new Date().toISOString(),
        }, true);
        const msgs = await podReadLongChatRecent(roomId, 2);
        expect(msgs.some((m) => m.content === 'hello from the pod')).toBe(true);
    });

    it('Phase 2 (R103): drops an encrypted DM envelope to a peer inbox and reads it (no gateway)', async () => {
        asBob();
        expect(await podEnsureDmInbox()).toBeTruthy();   // Bob exposes his DM inbox

        asAlice();                                        // Alice drops a sealed envelope for Bob
        const ct = 'ciphertext-' + uid('ct');
        const envelope = {
            from_webid: alice.webId, ciphertext: ct, nonce: 'nonce-b64u',
            ratchet_pub: 'rpub-b64u', msg_num: 0, pn: 0, e2e: true,
        };
        expect(await podDropDm(bob.storageRoot, envelope)).toBe(true);

        asBob();                                          // Bob receives it from his own pod
        const drops = await podReadDmDrops();
        const got = drops.find((d) => d.envelope && d.envelope.ciphertext === ct);
        expect(got).toBeTruthy();
        expect(got.envelope.from_webid).toBe(alice.webId);
        expect(got.envelope.e2e).toBe(true);

        expect(await podDeleteDmDrop(got.url)).toBe(true);          // consume it
        const after = await podReadDmDrops();
        expect(after.some((d) => d.url === got.url)).toBe(false);
    });

    it('R103 engine: webdm.dropDm delivers and drainOnce receives + deletes (no gateway)', async () => {
        // Real pod, real pod.js; a stub ratchet so we assert the engine end to end
        // (the two-party ratchet itself is covered by unit tests + the browser smoke,
        // since e2e.js is a single-identity module).
        const podFns = { podEnsureDmInbox, podDropDm, podReadDmDrops, podDeleteDmDrop };
        const e2eStub = { cachePeerPub() {}, ratchetDecrypt: async () => { throw new Error('unused'); } };

        asBob();
        const bobDm = createWebDm({
            pod: podFns, e2e: e2eStub, notify: null,
            handleEvent: () => {}, getSelfWebId: () => bob.webId, getDisplayName: () => 'Bob',
        });
        expect(await bobDm.start()).toBeTruthy();   // ensures Bob's inbox

        asAlice();
        const aliceDm = createWebDm({
            pod: podFns, e2e: e2eStub, notify: null,
            handleEvent: () => {}, getSelfWebId: () => alice.webId, getDisplayName: () => 'Alice',
        });
        const text = 'plain-' + uid('t');
        expect(await aliceDm.dropDm({ target_webid: bob.webId, message_id: uid('m'), content: text, e2e: false })).toBe(true);

        asBob();
        const received = [];
        const bobDm2 = createWebDm({
            pod: podFns, e2e: e2eStub, notify: null,
            handleEvent: (ev) => received.push(ev), getSelfWebId: () => bob.webId, getDisplayName: () => 'Bob',
        });
        await bobDm2.drainOnce();
        const msg = received.find((e) => e.type === 'message' && e.content === text);
        expect(msg).toBeTruthy();
        expect(msg).toMatchObject({ from_webid: alice.webId, source: 'local_dm', _persistDm: true });
        expect((await podReadDmDrops()).length).toBe(0);   // consumed
    });

    it('R103: a drop to a peer with no DM inbox fails (surfaces as not delivered)', async () => {
        // Alice never created her DM inbox in this suite, so Bob messaging her is
        // a POST into a missing container: CSS returns 404 and podDropDm reports
        // false, which is what stops the PodSocket from echoing a false success.
        asBob();
        expect(await podDropDm(alice.storageRoot, { from_webid: bob.webId, content: 'ct', e2e: false })).toBe(false);
    });

    it('R103 multi-device: fanout drops one envelope per device; each device claims its own', async () => {
        const podFns = { podEnsureDmInbox, podDropDm, podReadDmDrops, podDeleteDmDrop };
        const e2eStub = { cachePeerPub() {}, ratchetDecrypt: async () => 'x' };
        asBob();
        const bobDm = createWebDm({ pod: podFns, e2e: e2eStub, notify: null, handleEvent: () => {}, getSelfWebId: () => bob.webId, getMyDeviceId: () => 'bob-A' });
        await bobDm.start();

        asAlice();   // Alice fans out to two of Bob's devices
        const aliceDm = createWebDm({ pod: podFns, e2e: e2eStub, notify: null, handleEvent: () => {}, getSelfWebId: () => alice.webId, getMyDeviceId: () => 'alice-1' });
        const mid = uid('m');
        await aliceDm.dropFanout({ message_id: mid, fanout: [
            { to_webid: bob.webId, to_device_id: 'bob-A', payload: { content: 'ctA', e2e: false } },
            { to_webid: bob.webId, to_device_id: 'bob-B', payload: { content: 'ctB', e2e: false } },
        ] });

        asBob();     // device bob-A claims only its own copy, leaving bob-B's
        const seen = [];
        const bobA = createWebDm({ pod: podFns, e2e: e2eStub, notify: null, handleEvent: (e) => seen.push(e), getSelfWebId: () => bob.webId, getMyDeviceId: () => 'bob-A' });
        await bobA.drainOnce();
        expect(seen.filter((e) => e.type === 'dm_fanout' && e.message_id === mid)).toHaveLength(1);
        expect(seen[0].to_device_id).toBe('bob-A');
        const left = await podReadDmDrops();
        expect(left.some((d) => d.envelope.to_device_id === 'bob-B')).toBe(true);   // sibling copy remains
        expect(left.some((d) => d.envelope.to_device_id === 'bob-A')).toBe(false);  // ours consumed
    });

    it('R106: room-join request/approve handshake over the pod (no gateway)', async () => {
        const podFns = { podEnsureJoinInbox, podDropJoin, podReadJoins, podDeleteJoin, podWritePendingJoin };
        const peerRoot = (w) => w.replace(/profile\/card#me$/, '');
        const roomId = uid('room');
        const requests = [];
        const approvals = [];

        const aliceJoin = createWebJoin({   // owner
            pod: podFns, notify: null, getSelfWebId: () => alice.webId, getDisplayName: () => 'Alice',
            getSelfPodRoot: () => alice.storageRoot, peerPodRoot: peerRoot, onJoinRequest: (m) => requests.push(m),
        });
        const bobJoin = createWebJoin({     // joiner
            pod: podFns, notify: null, getSelfWebId: () => bob.webId, getDisplayName: () => 'Bob',
            getSelfPodRoot: () => bob.storageRoot, peerPodRoot: peerRoot, onApproved: (m) => approvals.push(m),
        });

        asAlice(); expect(await aliceJoin.start()).toBeTruthy();   // owner's join inbox
        asBob(); expect(await bobJoin.start()).toBeTruthy();       // joiner's join inbox

        asBob(); expect(await bobJoin.requestJoin(roomId, alice.webId)).toBe(true);   // Bob asks
        expect(await podHasPendingJoin(roomId, alice.webId)).toBe(true);

        asAlice(); await aliceJoin.drainOnce();                    // Alice sees the request
        const req = requests.find((r) => r.room_id === roomId);
        expect(req).toBeTruthy();
        expect(req.from_webid).toBe(bob.webId);
        await aliceJoin.sendApproval(bob.webId, roomId, 'General');   // Alice approves

        asBob(); await bobJoin.drainOnce();                        // Bob gets the approval
        const appr = approvals.find((a) => a.room_id === roomId);
        expect(appr).toBeTruthy();
        expect(appr.owner_webid).toBe(alice.webId);
        expect(appr.owner_pod_root).toBe(alice.storageRoot);       // so Bob knows where the room lives
        expect(await podHasPendingJoin(roomId, alice.webId)).toBe(true);
        const record = { roomId, owner: alice.webId, ownerPodRoot: alice.storageRoot,
            container: chatRootUrl(alice.storageRoot, roomId), title: 'General' };
        expect(await podWriteJoinedRooms([record])).toBe(true);
        const persisted = await bobSession.fetch(bob.storageRoot + 'proxion/joined-rooms.json',
            { headers: { Accept: 'application/json' } });
        expect(persisted.ok).toBe(true);
        expect(persisted.headers.get('content-type')).toMatch(/^application\/json\b/);
        expect((await persisted.json())['px:rooms']).toEqual([record]);
        await podDeletePendingJoin(roomId, alice.webId);
        expect(await podHasPendingJoin(roomId, alice.webId)).toBe(false);
        // A new module instance models a reload: no in-memory state supplies the
        // nested room fields that a pod's RDF conversion can discard.
        const { createPodIo } = await import('./podio.js');
        const reloaded = createPodIo({ fetch: bobSession.fetch, isLoggedIn: () => true,
            storageRoot: () => bob.storageRoot, webId: () => bob.webId });
        expect(await reloaded.podReadJoinedRooms()).toEqual([record]);
    });

    it('R106: after approval a joiner can read AND post to the owner room (no gateway)', async () => {
        asAlice();
        const roomId = uid('room');
        const chat = chatRootUrl(alice.storageRoot, roomId);
        // Owner seeds the room (creating the chat container) and posts.
        expect(await podWriteChatMessageAt(chat, uid('m'), { content: 'from alice', from_webid: alice.webId, timestamp: new Date().toISOString() })).not.toBe(false);
        // Owner grants the joiner read+append on the chat container.
        await podGrantChatParticipants(chat, alice.webId, [bob.webId]);

        asBob();
        const bobsRead = await podReadChatRecentAt(chat);          // joiner can read
        expect(bobsRead.some((m) => m.content === 'from alice')).toBe(true);
        expect(await podWriteChatMessageAt(chat, uid('m'), { content: 'from bob', from_webid: bob.webId, timestamp: new Date().toISOString() })).not.toBe(false);   // joiner can post

        asAlice();
        const alicesRead = await podReadChatRecentAt(chat);        // owner sees the joiner's message
        expect(alicesRead.some((m) => m.content === 'from bob')).toBe(true);
    });

    it('R104: publishes a presence heartbeat a peer reads as online (no gateway)', async () => {
        asAlice();
        expect(await podWritePresence('online')).toBe(true);   // Alice publishes, public-read

        asBob();                                               // Bob reads Alice's heartbeat
        const doc = await podReadPresence(alice.storageRoot);
        expect(doc).toBeTruthy();
        expect(doc.status).toBe('online');
        expect(doc.heartbeat).toBeGreaterThan(0);
        expect(statusFromHeartbeat(doc, Date.now()).status).toBe('online');
        // A stale heartbeat decays to offline.
        expect(statusFromHeartbeat(doc, doc.heartbeat + 10 * 60 * 1000).status).toBe('offline');
    });

    it('R105: drops a call signal to a peer inbox and reads it back (no gateway)', async () => {
        asBob();
        expect(await podEnsureCallInbox()).toBeTruthy();       // Bob exposes his call inbox

        asAlice();                                             // Alice sends an offer signal
        const offer = { type: 'voice_invite', from_webid: alice.webId, caller_webid: alice.webId, sdp_offer: 'SDP-' + uid('o'), fp_sig: 'SIG' };
        expect(await podDropSignal(bob.storageRoot, offer)).toBe(true);

        asBob();                                               // Bob receives it from his pod
        const sigs = await podReadSignals();
        const got = sigs.find((s) => s.signal && s.signal.sdp_offer === offer.sdp_offer);
        expect(got).toBeTruthy();
        expect(got.signal).toMatchObject({ type: 'voice_invite', from_webid: alice.webId, fp_sig: 'SIG' });
        expect(await podDeleteSignal(got.url)).toBe(true);
        expect((await podReadSignals()).some((s) => s.url === got.url)).toBe(false);
    });

    it('rejects an actual stale joined-room write with HTTP 412', async () => {
        const makeIo = () => createPodIo({ fetch: aliceSession.fetch, isLoggedIn: () => true,
            storageRoot: () => alice.storageRoot, webId: () => alice.webId });
        const a = makeIo(), b = makeIo();
        expect(await a.podWriteJoinedRooms([])).toBe(true);
        const [first, stale] = await Promise.all([a.podReadJoinedRoomsVersion(), b.podReadJoinedRoomsVersion()]);
        expect(first.exists).toBe(true);
        expect(first.etag).toMatch(/^".+"$/);
        expect(stale.etag).toBe(first.etag);
        const roomId = uid('joined-a');
        const record = { roomId, owner: bob.webId, ownerPodRoot: bob.storageRoot,
            container: chatRootUrl(bob.storageRoot, roomId), title: 'First device' };
        expect(await a.podWriteJoinedRoomsVersion(withJoinedRecord(first.records, record), first)).toEqual({ ok: true, conflict: false });
        expect(await b.podWriteJoinedRoomsVersion(stale.records, stale)).toEqual({ ok: false, conflict: true });
        expect(await makeIo().podReadJoinedRooms()).toEqual([record]);
    });

    it('retries concurrent joined-room approvals from independent devices against real pod ETags', async () => {
        const resource = alice.storageRoot + 'proxion/joined-rooms.json';
        let initialReads = 0, conflicts = 0, release;
        const barrier = new Promise((resolve) => { release = resolve; });
        const fetch = async (url, opts = {}) => {
            const response = await aliceSession.fetch(url, opts);
            if (url === resource && (!opts.method || opts.method === 'GET') && initialReads++ < 2) {
                if (initialReads === 2) release();
                await barrier;
            }
            if (response.status === 412) conflicts++;
            return response;
        };
        const device = () => {
            const io = createPodIo({ fetch, isLoggedIn: () => true,
                storageRoot: () => alice.storageRoot, webId: () => alice.webId });
            return createJoinedRoomStore({ readVersion: io.podReadJoinedRoomsVersion,
                writeVersion: io.podWriteJoinedRoomsVersion, getWebId: () => alice.webId });
        };
        const first = device(), second = device();
        const record = (title) => {
            const roomId = uid('joined-' + title);
            return { roomId, owner: bob.webId, ownerPodRoot: bob.storageRoot,
                container: chatRootUrl(bob.storageRoot, roomId), title };
        };
        const one = record('one'), two = record('two');
        expect(await Promise.all([
            first.update((list) => withJoinedRecord(list, one)),
            second.update((list) => withJoinedRecord(list, two)),
        ])).toEqual([true, true]);
        expect(conflicts).toBeGreaterThanOrEqual(1);
        const reloaded = await device().list();
        expect(reloaded).toEqual(expect.arrayContaining([one, two]));
        expect(new Set(reloaded.map((r) => r.roomId)).size).toBe(reloaded.length);
    });

    it('converges simultaneous owner approvals to the latest descriptor, ACL, and member list', async () => {
        const roomId = uid('owner-race');
        const resource = alice.storageRoot + 'rooms/' + roomId + '/room.json';
        const chat = chatRootUrl(alice.storageRoot, roomId);
        let initialReads = 0, conflicts = 0, release;
        const barrier = new Promise((resolve) => { release = resolve; });
        const fetch = async (url, opts = {}) => {
            const response = await aliceSession.fetch(url, opts);
            if (url === resource && (!opts.method || opts.method === 'GET') && initialReads++ < 2) {
                if (initialReads === 2) release();
                await barrier;
            }
            if (response.status === 412) conflicts++;
            return response;
        };
        const io = createPodRoomIo({ fetch, isLoggedIn: () => true,
            storageRoot: () => alice.storageRoot, webId: () => alice.webId });
        expect(await io.ensureChatIndexAt(chat, 'Owner race')).toBe(true);
        expect(await io.podWriteRoomDescriptor(buildRoomDescriptor({ roomId, title: 'Owner race',
            owner: alice.webId, members: [{ webid: alice.webId, role: 'owner' }] }))).toBe(true);
        const device = () => createOwnerMembership({
            read: io.podReadRoomDescriptorVersion, write: io.podWriteRoomDescriptorVersion,
            getOwnerId: () => alice.webId,
            apply: async (descriptor, isCurrent, version) => {
                const members = descriptor.members.filter((m) => !m.banned).map((m) => m.webid);
                if (!(await io.podGrantChatParticipants(chat, alice.webId, members.filter((webid) => webid !== alice.webId), version))) return false;
                return io.podWriteRoomMembers(roomId, members.map((webid) => ({ webid, role: webid === alice.webId ? 'admin' : 'member' })));
            },
        });
        const peer = alice.storageRoot + 'profile/card#another-device';
        const approve = (webid) => (descriptor) => withMembers(descriptor,
            [...descriptor.members, { webid, role: 'member' }]);
        const result = await Promise.all([device().update(roomId, approve(bob.webId)), device().update(roomId, approve(peer))]);
        expect(result.every((r) => r.ok)).toBe(true);
        expect(conflicts).toBeGreaterThanOrEqual(1);
        const final = await io.podReadRoomDescriptorVersion(roomId);
        expect(final.etag).toMatch(/^".+"$/);
        expect(final.descriptor.members.map((m) => m.webid).sort()).toEqual([alice.webId, bob.webId, peer].sort());
        expect((await io.podReadRoomMembersAt(chat)).map((m) => m.webid).sort()).toEqual([alice.webId, bob.webId, peer].sort());
        expect((await bobSession.fetch(chat + 'index.ttl')).ok).toBe(true);
    });

    it('rejects a paused approval ACL write after a completed room closure', async () => {
        const roomId = uid('close-race');
        const chat = chatRootUrl(alice.storageRoot, roomId);
        let unblock, reached;
        const release = new Promise(resolve => { unblock = resolve; });
        const paused = new Promise(resolve => { reached = resolve; });
        let delay = true, conflicts = 0;
        const fetch = async (url, opts = {}) => {
            if (delay && url === chat + '.acl' && opts.method === 'PUT' && opts.body.includes(`<${bob.webId}>`)) {
                delay = false;
                reached();
                await release;
            }
            const response = await aliceSession.fetch(url, opts);
            if (response.status === 412) conflicts++;
            return response;
        };
        const io = createPodRoomIo({ fetch, isLoggedIn: () => true,
            storageRoot: () => alice.storageRoot, webId: () => alice.webId });
        expect(await io.ensureChatIndexAt(chat, 'Closure race')).toBe(true);
        expect(await io.podWriteRoomDescriptor(buildRoomDescriptor({ roomId, owner: alice.webId }))).toBe(true);
        expect(await io.podGrantChatParticipants(chat, alice.webId, [])).toBe(true);
        const device = () => createOwnerMembership({
            read: io.podReadRoomDescriptorVersion, write: io.podWriteRoomDescriptorVersion,
            getOwnerId: () => alice.webId,
            apply: async (descriptor, isCurrent, version) => {
                const members = descriptor.members.filter(m => !m.banned).map(m => m.webid);
                if (!(await io.podGrantChatParticipants(chat, alice.webId, members.filter(w => w !== alice.webId), version))) return false;
                return io.podWriteRoomMembers(roomId, members.map(webid => ({ webid, role: webid === alice.webId ? 'admin' : 'member' })));
            },
        });
        const approval = device().update(roomId, d => withMembers(d, [...d.members, { webid: bob.webId, role: 'member' }]));
        try {
            await paused;
            expect((await device().remove(roomId, async () => true)).ok).toBe(true);
            expect((await bobSession.fetch(chat + 'index.ttl')).status).toBe(403);
        } finally { unblock(); }
        expect(await approval).toEqual({ ok: false, reason: 'room_closed' });
        expect(conflicts).toBeGreaterThanOrEqual(1);
        expect((await bobSession.fetch(chat + 'index.ttl')).status).toBe(403);
        const final = await io.podReadRoomDescriptorVersion(roomId);
        expect(final.descriptor).toMatchObject({ closed: true, deletion_complete: true });
        expect(final.descriptor.members.map(m => m.webid)).toEqual([alice.webId]);
    }, 30000);

    it('finishes browser-host cleanup through the actual pod.js adapter', async () => {
        asAlice();
        const roomId = uid('browser-close');
        const chat = chatRootUrl(alice.storageRoot, roomId);
        const io = createPodRoomIo({ fetch: aliceSession.fetch, isLoggedIn: () => true,
            storageRoot: () => alice.storageRoot, webId: () => alice.webId });
        expect(await io.ensureChatIndexAt(chat, 'Browser close')).toBe(true);
        expect(await io.podWriteRoomDescriptor(buildRoomDescriptor({ roomId, owner: alice.webId,
            members: [{ webid: bob.webId, role: 'member' }] }))).toBe(true);
        expect(await io.podRegisterChat(chat)).toBe(true);
        const owner = createOwnerMembership({ read: io.podReadRoomDescriptorVersion,
            write: io.podWriteRoomDescriptorVersion, getOwnerId: () => alice.webId,
            apply: async (d, isCurrent, version) => {
                if (!(await io.podGrantChatParticipants(chat, alice.webId, d.members.filter(m => m.webid !== alice.webId).map(m => m.webid), version))) return false;
                return io.podWriteRoomMembers(roomId, [{ webid: alice.webId, role: 'admin' }]);
            },
        });
        const cached = await io.podReadRoomDescriptor(roomId);
        const resource = `${alice.storageRoot}rooms/${roomId}/room.json`;
        // Simulate a browser still holding the pre-closure GET representation.
        // Cleanup must validate a fresh snapshot before removing discovery.
        _session = { info: aliceSession.info, fetch: async (url, opts = {}) => {
            if (url === resource && !opts.method && opts.cache !== 'no-store') {
                return new Response(JSON.stringify(cached), { status: 200 });
            }
            return aliceSession.fetch(url, opts);
        } };
        try {
            expect(await owner.remove(roomId, () => podDeleteOwnedRoom(roomId))).toMatchObject({ ok: true });
        } finally { asAlice(); }
        expect((await io.podReadRoomDescriptor(roomId)).deletion_complete).toBe(true);
    });

    it('keeps owner-signed pins readable to members across old days and conditional updates', async () => {
        const key = await makeIdentity(), roomId = uid('pins'), chat = chatRootUrl(alice.storageRoot, roomId);
        const signer = { privKey: key.priv, signerDid: key.did };
        const io = createPodRoomIo({ fetch: aliceSession.fetch, isLoggedIn: () => true,
            storageRoot: () => alice.storageRoot, webId: () => alice.webId,
            getSigner: () => signer, fetchPeerSigner: async () => ({ signer: key.did }) });
        const peer = createPodRoomIo({ fetch: bobSession.fetch, isLoggedIn: () => true,
            storageRoot: () => bob.storageRoot, webId: () => bob.webId,
            getSigner: () => signer, fetchPeerSigner: async () => ({ signer: key.did }) });
        expect(await io.ensureChatIndexAt(chat, 'Pins')).toBe(true);
        expect(await io.podGrantChatParticipants(chat, alice.webId, [bob.webId])).toBe(true);
        const old = '2026-01-01T12:00:00.000Z';
        for (const id of ['first', 'second']) expect(await io.podWriteChatMessageAt(chat, id,
            { timestamp: old, content: 'Pinned ' + id, from_webid: alice.webId })).toBe(true);
        expect(await io.podSetPinAt(chat, 'first', old, true, alice.webId)).toMatchObject({ ok: true });
        expect((await peer.podReadPinsAt(chat, alice.webId)).pins[0]).toMatchObject({ message_id: 'first', content: 'Pinned first' });
        expect(await peer.podSetPinAt(chat, 'second', old, true, alice.webId)).toMatchObject({ ok: false, reason: 'forbidden' });
        expect((await bobSession.fetch(chat + 'pins.json', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status).toBe(403);
        const other = createPodRoomIo({ fetch: aliceSession.fetch, isLoggedIn: () => true,
            storageRoot: () => alice.storageRoot, webId: () => alice.webId,
            getSigner: () => signer, fetchPeerSigner: async () => ({ signer: key.did }) });
        const results = await Promise.all([
            io.podSetPinAt(chat, 'first', old, false, alice.webId),
            other.podSetPinAt(chat, 'second', old, true, alice.webId),
        ]);
        expect(results.every(result => result.ok)).toBe(true);
        expect((await peer.podReadPinsAt(chat, alice.webId)).pins.map(pin => pin.message_id)).toEqual(['second']);
        expect(await io.podChangeMessageAt(chat, 'second', old, { kind: 'edit', content: 'Latest pinned text' }, alice.webId)).toMatchObject({ ok: true });
        expect((await peer.podReadPinsAt(chat, alice.webId)).pins[0].content).toBe('Latest pinned text');
        const latest = (await io.podReadChatDayAt(chat, old, '', alice.webId)).find(message => message.message_id === 'second');
        expect(await io.podChangeMessageAt(chat, 'second', old, { kind: 'delete', expectedRevision: latest.revision_id }, alice.webId)).toMatchObject({ ok: true });
        expect((await peer.podReadPinsAt(chat, alice.webId)).pins).toEqual([]);
    }, 30000);

});
