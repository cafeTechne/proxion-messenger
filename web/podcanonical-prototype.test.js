/**
 * PLAN_ROUND_69 open question #4 — prototype the authority model against a live
 * pod BEFORE building D1/D2.
 *
 * Claim under test: "the pod is the durable log of message CONTENT." If true, a
 * room's content must fully reconstruct from the pod alone with an EMPTY local
 * base (a simulated gateway/SQLite wipe), including the Phase B edit/delete
 * mutations, and a local-only un-acked message must survive as an overlay on top
 * of the pod base (the D1 merge direction: pod is the spine, local overlays).
 *
 * What this prototype deliberately does NOT reconstruct: room governance
 * (membership, roles). That is not in the Long Chat, only messages are, which is
 * exactly framing tension #3 (governance stays gateway-side). Recorded here so the
 * limit is proven, not assumed.
 *
 * Run (skipped when TEST_CSS_CLIENT_ID is unset):
 *   docker compose -f docker-compose.test.yml up -d css-alice   # or npx CSS
 *   python scripts/provision_test_pod.py
 *   cd web && set -a && . ./.env.test && set +a && npx vitest run podcanonical-prototype.test.js
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';

let _session = null;
let _storageRoot = null;

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import {
    podWriteLongChatMessage, podEditLongChatMessage, podSoftDeleteLongChatMessage,
    podHydrateRoom, ensureProxionContainer,
} from './pod.js';
import { chatRootUrl, dayPath } from './longchat.js';

const LIVE = !!process.env.TEST_CSS_CLIENT_ID;
// TODAY (UTC): podHydrateRoom reads a recent window ending now, so the messages
// have to be dated within it. Fixed times on the same day keep ordering
// deterministic; the reads below use a 2-day window to be safe across midnight.
const DAY = new Date().toISOString().slice(0, 10);   // YYYY-MM-DD (UTC)
const T0 = `${DAY}T09:00:00.000Z`;
const T1 = `${DAY}T09:01:00.000Z`;
const T2 = `${DAY}T09:02:00.000Z`;
const WINDOW = 2;
const ROOM = `pc-${Math.random().toString(36).slice(2, 10)}`;

function webId() {
    return process.env.TEST_WEBID || (_session && _session.info && _session.info.webId) || '';
}

beforeAll(async () => {
    if (!LIVE) return;
    const { Session } = await import('@inrupt/solid-client-authn-node');
    _session = new Session();
    await _session.login({
        clientId: process.env.TEST_CSS_CLIENT_ID,
        clientSecret: process.env.TEST_CSS_CLIENT_SECRET,
        oidcIssuer: process.env.TEST_CSS_ISSUER,
    });
    _storageRoot = process.env.TEST_STORAGE_ROOT;
    await ensureProxionContainer();

    const me = webId();
    // Three messages, then edit the middle and delete the last: the room's final
    // truth is "first" / "second (edited)" / "[deleted]".
    await podWriteLongChatMessage(ROOM, 'm1', { content: 'first', from_webid: me, timestamp: T0 });
    await podWriteLongChatMessage(ROOM, 'm2', { content: 'second', from_webid: me, timestamp: T1 });
    await podWriteLongChatMessage(ROOM, 'm3', { content: 'third', from_webid: me, timestamp: T2 });
    await podEditLongChatMessage(ROOM, 'm2', T1, 'second (edited)');
    await podSoftDeleteLongChatMessage(ROOM, 'm3', T2, `${DAY}T10:00:00.000Z`);
}, 90000);

afterAll(async () => {
    if (!LIVE || !_session) return;
    const base = chatRootUrl(_storageRoot, ROOM);
    const d = dayPath(T0);
    const [yy, mm] = d.split('/');
    for (const u of [`${base}${d}/chat.ttl`, `${base}${d}/`, `${base}${yy}/${mm}/`,
        `${base}${yy}/`, `${base}index.ttl`, base]) {
        try { await _session.fetch(u, { method: 'DELETE' }); } catch { /* ignore */ }
    }
    await _session.logout();
}, 60000);

describe.skipIf(!LIVE)('the pod reconstructs a room\'s content across a gateway wipe', () => {
    it('rebuilds the full, ordered final state from the pod with an EMPTY local base', async () => {
        // local = [] is the wiped gateway/SQLite: nothing but the pod remains.
        const msgs = await podHydrateRoom(ROOM, { days: WINDOW, local: [] });
        const byId = Object.fromEntries(msgs.map(m => [m.message_id, m]));

        // All three nodes survive (a delete is a tombstone, not a removal).
        expect(msgs.map(m => m.message_id)).toEqual(['m1', 'm2', 'm3']);   // oldest-first

        // Content is the FINAL truth, not the original: the edit and delete both
        // reconstruct from the pod alone.
        expect(byId.m1.content).toBe('first');
        expect(byId.m2.content).toBe('second (edited)');
        expect(byId.m3.deleted).toBe(true);
        expect(byId.m3.content).toBe('');

        // Author and time survived the round trip for every message.
        for (const m of msgs) {
            expect(m.from_webid).toBe(webId());
            expect(String(m.timestamp)).toContain(DAY);
        }
    }, 60000);

    it('overlays a local-only un-acked message on top of the pod base (D1 merge)', async () => {
        // A message the client sent but whose pod write has not landed yet: it is
        // in local state only, with an id the pod has never seen.
        const unacked = { message_id: 'local-pending', content: 'not yet in the pod',
            from_webid: webId(), timestamp: T2 + '1' };
        const msgs = await podHydrateRoom(ROOM, { days: WINDOW, local: [unacked] });

        // The pod is the base; the local-only message appears too, ordered by time.
        expect(msgs.map(m => m.message_id)).toContain('local-pending');
        expect(msgs.find(m => m.message_id === 'local-pending').content).toBe('not yet in the pod');
        // Pod content is still authoritative for ids the pod DOES have.
        expect(msgs.find(m => m.message_id === 'm2').content).toBe('second (edited)');
    }, 60000);

    it('reconstructs MESSAGES only, not governance (framing tension #3)', async () => {
        const msgs = await podHydrateRoom(ROOM, { days: WINDOW, local: [] });
        // The Long Chat carries messages; there is no membership/role data to
        // rebuild. Every reconstructed item is a message, nothing else. This is why
        // governance stays gateway-side and is out of scope for D.
        for (const m of msgs) {
            expect(m).toHaveProperty('message_id');
            expect(m).toHaveProperty('content');
            expect(m).not.toHaveProperty('members');
            expect(m).not.toHaveProperty('role');
        }
    }, 60000);
});
