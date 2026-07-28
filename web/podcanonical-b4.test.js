/**
 * PLAN_ROUND_71 B4 — prototype single-pod rooms against a live pod, BEFORE deciding
 * whether to adopt them.
 *
 * Today gateway rooms mirror PER USER (each client writes its own sends to its own
 * pod), so a rehydrated room only recovers the HOST's messages. B4 asks: can a
 * room's FULL history live in ONE pod (the host's), so it reconstructs completely?
 * The two-identity mechanism is already proven (longchat-shared). This adds the two
 * room-specific unknowns:
 *   1. COMPLETENESS: reconstructing from the one pod yields ALL members' messages.
 *   2. CROSS-MEMBER ORDER: with two members appending to one day file under clock
 *      skew, the gateway-assigned px:seq (a single server clock, global across
 *      members) still reconstructs the true order.
 *
 * Needs TWO identities on the same CSS (ALICE_*, BOB_*); skipped otherwise.
 */
import { describe, it, expect, vi } from 'vitest';

let _session = null;
let _storageRoot = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import {
    podWriteChatMessageAt, podSetChatSeqAt, podReadChatRecentAt, podGrantChatParticipants,
} from './pod.js';
import { chatRootUrl, dayFileAt, indexUrlAt, dayPath } from './longchat.js';

const A = 'ALICE';
const B = 'BOB';
const LIVE = !!process.env[`${A}_TEST_CSS_CLIENT_ID`] && !!process.env[`${B}_TEST_CSS_CLIENT_ID`];

const DAY = new Date().toISOString().slice(0, 10);
const ROOM = `b4-${Math.random().toString(36).slice(2, 8)}`;

async function login(prefix) {
    const { Session } = await import('@inrupt/solid-client-authn-node');
    const s = new Session();
    await s.login({
        clientId: process.env[`${prefix}_TEST_CSS_CLIENT_ID`],
        clientSecret: process.env[`${prefix}_TEST_CSS_CLIENT_SECRET`],
        oidcIssuer: process.env[`${prefix}_TEST_CSS_ISSUER`],
    });
    return s;
}

describe.skipIf(!LIVE)('B4 prototype: a room fully reconstructs from ONE shared pod', () => {
    it('two members in one pod: complete + correctly ordered by gateway seq', async () => {
        const alice = await login(A);
        const bob = await login(B);
        const aliceRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
        const aliceWebId = process.env[`${A}_TEST_WEBID`];
        const bobWebId = process.env[`${B}_TEST_WEBID`];
        const container = chatRootUrl(aliceRoot, ROOM);   // the room lives in ALICE's pod

        // Global (gateway) send order: A1, then B1, then A2. But the clocks are
        // skewed: Alice's is ahead, Bob's is behind, so dct:created order disagrees.
        const asAlice = () => { _session = alice; _storageRoot = aliceRoot; };
        const asBob = () => { _session = bob; _storageRoot = process.env[`${B}_TEST_STORAGE_ROOT`]; };

        try {
            // Alice hosts + grants Bob write to the room container.
            asAlice();
            expect(await podWriteChatMessageAt(container, 'a1', {
                content: 'A1 (sent first)', from_webid: aliceWebId, timestamp: `${DAY}T09:05:00.000Z`,
            })).toBe(true);
            expect(await podGrantChatParticipants(container, aliceWebId, [bobWebId])).toBe(true);

            asBob();
            expect(await podWriteChatMessageAt(container, 'b1', {
                content: 'B1 (sent second)', from_webid: bobWebId, timestamp: `${DAY}T09:01:00.000Z`,
            })).toBe(true);

            asAlice();
            expect(await podWriteChatMessageAt(container, 'a2', {
                content: 'A2 (sent third)', from_webid: aliceWebId, timestamp: `${DAY}T09:06:00.000Z`,
            })).toBe(true);

            // Each member stamps the gateway's global order (single server clock) as
            // px:seq on their OWN message in the shared pod (Bob has write access).
            asAlice();
            await podSetChatSeqAt(container, 'a1', `${DAY}T09:05:00.000Z`, 1000);
            await podSetChatSeqAt(container, 'a2', `${DAY}T09:06:00.000Z`, 3000);
            asBob();
            await podSetChatSeqAt(container, 'b1', `${DAY}T09:01:00.000Z`, 2000);

            // Reconstruct the whole room from the one pod.
            asAlice();
            const msgs = await podReadChatRecentAt(container, 2, ROOM);

            // COMPLETENESS: all three messages, from both members.
            expect(msgs.map(m => m.message_id).sort()).toEqual(['a1', 'a2', 'b1']);
            expect(new Set(msgs.map(m => m.from_webid)).size).toBe(2);

            // CROSS-MEMBER ORDER: gateway seq order (A1, B1, A2), NOT the skewed
            // timestamp order (which would be B1, A1, A2).
            expect(msgs.map(m => m.message_id)).toEqual(['a1', 'b1', 'a2']);
        } finally {
            _session = alice; _storageRoot = aliceRoot;
            const d = dayPath(`${DAY}T09:00:00.000Z`); const [yy, mm] = d.split('/');
            for (const u of [
                dayFileAt(container, `${DAY}T09:00:00.000Z`), `${container}${d}/`,
                `${container}${yy}/${mm}/`, `${container}${yy}/`, indexUrlAt(container),
                `${container}.acl`, container,
            ]) { try { await alice.fetch(u, { method: 'DELETE' }); } catch { /* ignore */ } }
            await alice.logout(); await bob.logout();
        }
    }, 120000);
});
