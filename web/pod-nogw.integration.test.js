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
} from './pod.js';
import { buildRoomDescriptor } from './roomdesc.js';
import { createWebDm } from './webdm.js';
import { statusFromHeartbeat } from './webpresence.js';

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
});
