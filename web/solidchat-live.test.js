/**
 * solidchat.js against a LIVE pod, two identities. Proves the conversation MODEL
 * (host / grant / join / send / load), not just the raw primitives.
 *
 * Needs ALICE_* and BOB_* credentials (two provisioned pods). Skipped otherwise.
 *   docker compose -f docker-compose.test.yml up -d css-alice
 *   TEST_POD_EMAIL=a@x python scripts/provision_test_pod.py  # -> ALICE_*
 *   TEST_POD_EMAIL=b@x python scripts/provision_test_pod.py  # -> BOB_*
 *   cd web && npx vitest run solidchat-live.test.js
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
let _root = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import { createSolidChat } from './solidchat.js';
import { dayFileAt, indexUrlAt, dayPath } from './longchat.js';

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

beforeEach(() => {
    // Fresh conversation list per test.
    const store = {};
    global.localStorage = {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: (k) => { delete store[k]; },
    };
});

describe.skipIf(!LIVE)('solidchat model over a live shared pod', () => {
    it('host, other joins, both post, both read the same conversation', async () => {
        const alice = await login(A);
        const bob = await login(B);
        const aRoot = process.env[`${A}_TEST_STORAGE_ROOT`];
        const bRoot = process.env[`${B}_TEST_STORAGE_ROOT`];
        const bobWebId = process.env[`${B}_TEST_WEBID`];
        const aliceWebId = process.env[`${A}_TEST_WEBID`];

        let container = null;
        try {
            // Alice hosts and posts.
            _session = alice; _root = aRoot;
            const scA = createSolidChat({});
            const conv = await scA.hostConversation({ title: 'Cross-app chat', participantWebIds: [bobWebId] });
            expect(conv).toBeTruthy();               // grant + container setup worked
            container = conv.id;
            expect(await scA.sendMessage(conv.id, 'Alice: hi from Proxion')).toBe(true);

            // Bob joins the shared URL and posts.
            _session = bob; _root = bRoot;
            const scB = createSolidChat({});
            const joined = await scB.joinConversation(container, { title: 'Cross-app chat' });
            expect(joined).toBeTruthy();             // Bob has read access
            expect(await scB.sendMessage(container, 'Bob: hi from another identity')).toBe(true);

            // Both see both messages, correctly attributed.
            const bobView = await scB.loadConversation(container, 1);
            expect(bobView.map(m => m.content).sort()).toEqual(
                ['Alice: hi from Proxion', 'Bob: hi from another identity'].sort());

            _session = alice; _root = aRoot;
            const aliceView = await scA.loadConversation(container, 1);
            expect(aliceView).toHaveLength(2);
            const authors = new Set(aliceView.map(m => m.from_webid));
            expect(authors.has(aliceWebId)).toBe(true);
            expect(authors.has(bobWebId)).toBe(true);
        } finally {
            if (container) {
                _session = alice; _root = aRoot;
                const d = dayPath(new Date().toISOString()); const [yy, mm] = d.split('/');
                for (const u of [dayFileAt(container, new Date().toISOString()), `${container}${d}/`,
                    `${container}${yy}/${mm}/`, `${container}${yy}/`, indexUrlAt(container),
                    `${container}.acl`, container]) {
                    try { await alice.fetch(u, { method: 'DELETE' }); } catch { /* ignore */ }
                }
            }
            await alice.logout(); await bob.logout();
        }
    }, 120000);
});
