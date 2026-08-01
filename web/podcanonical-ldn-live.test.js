/**
 * PLAN_ROUND_75 G3 — Solid inbox (LDN) chat invites against a LIVE pod.
 *
 * Bob ensures his inbox (creates /inbox/, public-Append ACL, profile link). Alice,
 * a different identity, sends a chat invite to Bob's WebID. Bob reads his inbox and
 * sees the invitation. This proves the whole LDN path end to end, including that a
 * non-owner can Append to the inbox (the ACL) and the owner can read it back.
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
    podEnsureInbox, podDiscoverInbox, podSendChatInvite,
    podReadInboxNotifications, podDeleteInboxNotification,
} from './pod.js';

const A = 'ALICE';
const B = 'BOB';
const LIVE = !!process.env[`${A}_TEST_CSS_CLIENT_ID`] && !!process.env[`${B}_TEST_CSS_CLIENT_ID`];

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

describe.skipIf(!LIVE)('LDN chat invite crosses identities through the inbox', () => {
    it('Alice invites Bob; Bob reads the invitation from his inbox', async () => {
        const alice = await login(A);
        const bob = await login(B);
        const aliceRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
        const bobRoot = process.env[`${B}_TEST_STORAGE_ROOT`];
        const bobWebId = process.env[`${B}_TEST_WEBID`];
        const container = `${aliceRoot}proxion/rooms/ldn-${Math.random().toString(36).slice(2, 8)}/`;

        try {
            // Bob advertises an inbox others can post to.
            _session = bob; _storageRoot = bobRoot;
            const inbox = await podEnsureInbox();
            expect(inbox).toBeTruthy();
            expect(await podDiscoverInbox(bobWebId)).toBe(inbox);

            // Alice (a different identity) drops an invite in Bob's inbox.
            _session = alice; _storageRoot = aliceRoot;
            expect(await podSendChatInvite(bobWebId, { container, title: 'Team standup' })).toBe(true);

            // Bob reads it back.
            _session = bob; _storageRoot = bobRoot;
            const invites = await podReadInboxNotifications();
            const mine = invites.find(i => i.container === container);
            expect(mine).toBeTruthy();
            expect(mine.title).toBe('Team standup');
            expect(mine.from).toBe(process.env[`${A}_TEST_WEBID`]);
        } finally {
            _session = bob; _storageRoot = bobRoot;
            try {
                for (const i of await podReadInboxNotifications()) await podDeleteInboxNotification(i.id);
                const inbox = await podDiscoverInbox(bobWebId);
                if (inbox) {
                    await bob.fetch(inbox, { method: 'DELETE' });
                    await bob.fetch(inbox + '.acl', { method: 'DELETE' });
                }
            } catch { /* best-effort */ }
            await alice.logout(); await bob.logout();
        }
    }, 120000);
});
