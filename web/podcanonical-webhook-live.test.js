/**
 * PLAN_ROUND_77 K4 — CSS WebhookChannel2023 → our sendTo, against a LIVE pod.
 *
 * Subscribe an inbox to the server's webhook channel with sendTo = a local HTTP
 * receiver, then change the inbox (drop an invite). The server must POST our
 * receiver. This proves the novel leg that carries closed-app push: CSS → gateway.
 * (The gateway → browser push leg is covered by the Python unit tests.)
 *
 * Needs one identity on the same CSS (BOB_*); skipped otherwise.
 */
import { describe, it, expect, vi } from 'vitest';
import http from 'node:http';

let _session = null;
let _storageRoot = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import { podEnsureInbox, podDiscoverInbox, podReadInboxNotifications, podDeleteInboxNotification } from './pod.js';
import { subscribeWebhook } from './notify.js';
import { buildInviteNotification } from './ldn.js';

const B = 'BOB';
const LIVE = !!process.env[`${B}_TEST_CSS_CLIENT_ID`];

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

const until = async (pred, ms = 20000, step = 500) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (pred()) return true; await new Promise(r => setTimeout(r, step)); }
    return pred();
};

describe.skipIf(!LIVE)('CSS webhook channel delivers to our sendTo', () => {
    it('a change to the watched inbox POSTs the registered receiver', async () => {
        const bob = await login(B);
        const bobRoot = process.env[`${B}_TEST_STORAGE_ROOT`];
        const bobWebId = process.env[`${B}_TEST_WEBID`];
        _session = bob; _storageRoot = bobRoot;

        // A local receiver standing in for the gateway's /solid-webhook/{token}.
        const hits = [];
        const server = http.createServer((req, res) => { hits.push(req.method); res.writeHead(204); res.end(); });
        await new Promise((r) => server.listen(0, '127.0.0.1', r));
        const port = server.address().port;
        const sendTo = `http://127.0.0.1:${port}/solid-webhook/tok`;

        const inbox = await podEnsureInbox();
        expect(inbox).toBeTruthy();

        try {
            const subscribed = await subscribeWebhook(inbox, sendTo);
            expect(subscribed).toBe(true);

            // Change the inbox: drop an invite in it (bob may append to his own inbox).
            const res = await bob.fetch(inbox, {
                method: 'POST',
                headers: { 'Content-Type': 'application/ld+json' },
                body: JSON.stringify(buildInviteNotification({
                    from: bobWebId, to: bobWebId, container: `${bobRoot}proxion/rooms/wh-${Math.random().toString(36).slice(2, 8)}/`, title: 'Hook',
                })),
            });
            expect(res.ok).toBe(true);

            const delivered = await until(() => hits.length > 0);
            expect(delivered).toBe(true);
        } finally {
            await new Promise((r) => server.close(r));
            try {
                for (const i of await podReadInboxNotifications()) await podDeleteInboxNotification(i.id);
                const ib = await podDiscoverInbox(bobWebId);
                if (ib) { await bob.fetch(ib, { method: 'DELETE' }); await bob.fetch(ib + '.acl', { method: 'DELETE' }); }
            } catch { /* best-effort */ }
            await bob.logout();
        }
    }, 40000);
});
