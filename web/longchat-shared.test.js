/**
 * Shared cross-identity Long Chat against a LIVE Solid server.
 *
 * This guards the capability that makes "talk to other Solid chat apps" possible:
 * a chat lives in ONE pod, and a DIFFERENT identity, granted write access, can
 * post to it. Prototyped and confirmed end to end (including rendering in the
 * real SolidOS databrowser) before this was written; this locks it against
 * regression.
 *
 * Needs TWO provisioned identities on the same CSS. Skipped (not passed) unless
 * both credential sets are present:
 *
 *   docker compose -f docker-compose.test.yml up -d css-alice
 *   TEST_POD_EMAIL=alice@x python scripts/provision_test_pod.py  # -> ALICE_* env
 *   TEST_POD_EMAIL=bob@x   python scripts/provision_test_pod.py  # -> BOB_* env
 *   cd web && npx vitest run longchat-shared.test.js
 */
import { describe, it, expect, vi } from 'vitest';

// The tests drive two independent sessions directly, but pod.js reads the
// ambient solidSession/podStorageRoot, so we still mock auth.js and repoint it
// per operation.
let _session = null;
let _storageRoot = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import {
    podWriteChatMessageAt, podReadChatDayAt, podGrantChatParticipants,
} from './pod.js';
import { chatRootUrl, dayFileAt, indexUrlAt, dayPath } from './longchat.js';

const A = 'ALICE';
const B = 'BOB';
const LIVE = !!process.env[`${A}_TEST_CSS_CLIENT_ID`] && !!process.env[`${B}_TEST_CSS_CLIENT_ID`];

const TS = new Date().toISOString();
const ROOM = `shared-${Math.random().toString(36).slice(2, 8)}`;

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

describe.skipIf(!LIVE)('a second identity takes part in a shared Long Chat', () => {
    it('host grants a participant write, and both messages land with correct authors', async () => {
        const alice = await login(A);
        const bob = await login(B);
        const aliceWebId = process.env[`${A}_TEST_WEBID`];
        const bobWebId = process.env[`${B}_TEST_WEBID`];
        // The chat lives in ALICE's pod.
        const container = chatRootUrl(process.env[`${A}_TEST_STORAGE_ROOT`], ROOM);

        try {
            // 1. Alice (host) writes the first message into her own pod.
            _session = alice; _storageRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
            const w1 = await podWriteChatMessageAt(container, 'a1', {
                content: 'Alice: hello from Proxion', from_webid: aliceWebId, timestamp: TS,
                room_name: 'Shared room',
            });
            expect(w1).toBe(true);

            // 2. Alice grants Bob write access to the chat container.
            const granted = await podGrantChatParticipants(container, aliceWebId, [bobWebId]);
            expect(granted).toBe(true);

            // 3. THE capability: Bob, a different identity, posts to Alice's chat.
            _session = bob; _storageRoot = process.env[`${B}_TEST_STORAGE_ROOT`];
            const w2 = await podWriteChatMessageAt(container, 'b1', {
                content: 'Bob: replying from a different identity', from_webid: bobWebId, timestamp: TS,
            });
            expect(w2).toBe(true);

            // 4. Read the shared chat back: both messages, two distinct authors.
            _session = alice; _storageRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
            const msgs = await podReadChatDayAt(container, TS, ROOM);
            expect(msgs).toHaveLength(2);
            const byAuthor = Object.fromEntries(msgs.map(m => [m.from_webid, m.content]));
            expect(byAuthor[aliceWebId]).toContain('Alice');
            expect(byAuthor[bobWebId]).toContain('Bob');
            expect(new Set(msgs.map(m => m.from_webid)).size).toBe(2);

            // 5. And Bob can read it too (not just write).
            _session = bob; _storageRoot = process.env[`${B}_TEST_STORAGE_ROOT`];
            const bobView = await podReadChatDayAt(container, TS, ROOM);
            expect(bobView).toHaveLength(2);
        } finally {
            _session = alice; _storageRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
            const d = dayPath(TS); const [yy, mm] = d.split('/');
            for (const u of [
                dayFileAt(container, TS), `${container}${d}/`, `${container}${yy}/${mm}/`,
                `${container}${yy}/`, indexUrlAt(container), `${container}.acl`, container,
            ]) { try { await alice.fetch(u, { method: 'DELETE' }); } catch { /* ignore */ } }
            await alice.logout(); await bob.logout();
        }
    }, 120000);

    it('write to a chat with no known container is a no-op, not a throw', async () => {
        _session = await login(A); _storageRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
        await expect(podWriteChatMessageAt('', 'x', { content: 'x' })).resolves.toBe(false);
        await _session.logout();
    }, 60000);
});
