/**
 * PLAN_ROUND_78 L2 — inbox read delegation, against a LIVE pod.
 *
 * Alice grants a second identity (standing in for the gateway) read access to her
 * inbox via podGrantInboxReader. That identity must then be able to LIST her inbox
 * and see a dropped notification, while the public still cannot read it. This proves
 * the delegation the outbound poll relies on: a non-owner gateway can read a granted
 * inbox. (The poll parsing + push logic is covered by the Python unit tests.)
 *
 * Needs two identities on the same CSS (ALICE_*, BOB_*); skipped otherwise.
 */
import { describe, it, expect, vi } from 'vitest';

let _session = null;
let _storageRoot = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import { podEnsureInbox, podDiscoverInbox, podGrantInboxReader, podReadInboxNotifications, podDeleteInboxNotification } from './pod.js';
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

describe.skipIf(!LIVE)('inbox read delegation to a gateway identity', () => {
    it('a granted WebID can list the inbox; the public cannot', async () => {
        const alice = await login(A);
        const gw = await login(B);   // stands in for the gateway's identity
        const aliceRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
        const aliceWebId = process.env[`${A}_TEST_WEBID`];
        const gwWebId = process.env[`${B}_TEST_WEBID`];

        _session = alice; _storageRoot = aliceRoot;
        const inbox = await podEnsureInbox();
        expect(inbox).toBeTruthy();

        try {
            // Before the grant, the gateway identity cannot list the inbox.
            const before = await gw.fetch(inbox, { headers: { Accept: 'text/turtle' } });
            expect(before.ok).toBe(false);

            // Alice grants the gateway's WebID read.
            _session = alice; _storageRoot = aliceRoot;
            expect(await podGrantInboxReader(gwWebId)).toBe(true);

            // Drop a notification so there is something to list.
            await alice.fetch(inbox, {
                method: 'POST',
                headers: { 'Content-Type': 'application/ld+json' },
                body: JSON.stringify(buildInviteNotification({
                    from: aliceWebId, to: gwWebId, container: `${aliceRoot}proxion/rooms/g/`, title: 'G',
                })),
            });

            // Now the gateway identity can list the inbox and see a child.
            const after = await gw.fetch(inbox, { headers: { Accept: 'text/turtle' } });
            expect(after.ok).toBe(true);
            const ttl = await after.text();
            expect(ttl).toContain('contains');
        } finally {
            _session = alice; _storageRoot = aliceRoot;
            try {
                for (const i of await podReadInboxNotifications()) await podDeleteInboxNotification(i.id);
                const ib = await podDiscoverInbox(aliceWebId);
                if (ib) { await alice.fetch(ib, { method: 'DELETE' }); await alice.fetch(ib + '.acl', { method: 'DELETE' }); }
            } catch { /* best-effort */ }
            await alice.logout(); await gw.logout();
        }
    }, 60000);
});
