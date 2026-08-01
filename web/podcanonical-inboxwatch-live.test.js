/**
 * PLAN_ROUND_76 H3 — real-time inbox: a watched inbox delivers a live invite.
 *
 * Bob starts watchInbox on the conversation model. Alice drops an invite in Bob's
 * inbox. Bob's callback fires (within a poll interval) without Bob doing anything.
 * Exercises the real inbox + the real watchResource (WebSocket upgrade or polling)
 * against a live CSS pod.
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

import { podEnsureInbox, podDiscoverInbox, podDeleteInboxNotification, podReadInboxNotifications } from './pod.js';
import { createSolidChat } from './solidchat.js';
import { buildInviteNotification } from './ldn.js';

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

const until = async (pred, ms = 15000, step = 500) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (pred()) return true; await new Promise(r => setTimeout(r, step)); }
    return pred();
};

describe.skipIf(!LIVE)('watchInbox delivers a live invitation', () => {
    it('Bob is notified of Alice\'s invite without polling the UI himself', async () => {
        const alice = await login(A);
        const bob = await login(B);
        const bobRoot = process.env[`${B}_TEST_STORAGE_ROOT`];
        const bobWebId = process.env[`${B}_TEST_WEBID`];
        const container = `${process.env[`${A}_TEST_STORAGE_ROOT`]}proxion/rooms/watch-${Math.random().toString(36).slice(2, 8)}/`;

        _session = bob; _storageRoot = bobRoot;
        const inbox = await podEnsureInbox();
        expect(inbox).toBeTruthy();

        const got = [];
        const model = createSolidChat({});
        const stop = model.watchInbox((fresh) => got.push(...fresh), { intervalMs: 2000 });

        try {
            // Alice POSTs the invite herself (her own session), so Bob's watch keeps
            // Bob's session for the whole test — no session juggling races the poll.
            const res = await alice.fetch(inbox, {
                method: 'POST',
                headers: { 'Content-Type': 'application/ld+json' },
                body: JSON.stringify(buildInviteNotification({
                    from: process.env[`${A}_TEST_WEBID`], to: bobWebId, container, title: 'Live invite',
                })),
            });
            expect(res.ok).toBe(true);

            const delivered = await until(() => got.some(i => i.container === container));
            expect(delivered).toBe(true);
            const inv = got.find(i => i.container === container);
            expect(inv.title).toBe('Live invite');
        } finally {
            stop();
            _session = bob; _storageRoot = bobRoot;
            try {
                for (const i of await podReadInboxNotifications()) await podDeleteInboxNotification(i.id);
                const ib = await podDiscoverInbox(bobWebId);
                if (ib) { await bob.fetch(ib, { method: 'DELETE' }); await bob.fetch(ib + '.acl', { method: 'DELETE' }); }
            } catch { /* best-effort */ }
            await alice.logout(); await bob.logout();
        }
    }, 60000);
});
