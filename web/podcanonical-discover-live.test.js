/**
 * PLAN_ROUND_74 F1 — cross-identity chat discovery against a LIVE pod.
 *
 * Alice registers a chat (which ensures her PUBLIC type index, now public-readable,
 * and links it from her profile). Bob, a different identity, discovers Alice's
 * hosted chats by reading her WebID. This proves the whole path, including that the
 * public type index is actually readable by another party (the ACL fix).
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

import { podRegisterRoomChat, podDeregisterRoomChat, podListChatsForWebId } from './pod.js';
import { chatRootUrl } from './longchat.js';

const A = 'ALICE';
const B = 'BOB';
const LIVE = !!process.env[`${A}_TEST_CSS_CLIENT_ID`] && !!process.env[`${B}_TEST_CSS_CLIENT_ID`];
const ROOM = `disc-${Math.random().toString(36).slice(2, 8)}`;

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

describe.skipIf(!LIVE)('one identity discovers another\'s hosted chats', () => {
    it('Bob discovers a chat Alice hosts, by reading her WebID', async () => {
        const alice = await login(A);
        const bob = await login(B);
        const aliceRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
        const aliceWebId = process.env[`${A}_TEST_WEBID`];
        const container = chatRootUrl(aliceRoot, ROOM);

        try {
            // Alice registers a chat: ensures her public type index (public ACL),
            // links it from her profile, and registers the container.
            _session = alice; _storageRoot = aliceRoot;
            expect(await podRegisterRoomChat(ROOM)).toBe(true);

            // Bob, a different identity, discovers Alice's chats from her WebID.
            _session = bob; _storageRoot = process.env[`${B}_TEST_STORAGE_ROOT`];
            const chats = await podListChatsForWebId(aliceWebId);
            expect(chats.map(c => c.container)).toContain(container);
        } finally {
            _session = alice; _storageRoot = aliceRoot;
            try { await podDeregisterRoomChat(ROOM); } catch { /* ignore */ }
            try { await alice.fetch(`${aliceRoot}settings/publicTypeIndex.ttl`, { method: 'DELETE' }); } catch { /* ignore */ }
            try { await alice.fetch(`${aliceRoot}settings/publicTypeIndex.ttl.acl`, { method: 'DELETE' }); } catch { /* ignore */ }
            await alice.logout(); await bob.logout();
        }
    }, 120000);
});
