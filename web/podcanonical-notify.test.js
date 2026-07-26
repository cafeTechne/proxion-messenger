/**
 * PLAN_ROUND_70 Track A — real-time notifications against a LIVE pod, through the
 * REAL notify.js discovery + subscribe code (the direct v0.3 protocol). Verifies
 * the premise: CSS advertises a WebSocketChannel2023 service, accepts our
 * subscription, and PUSHES a notification when the day file changes, so real-time
 * does not depend on the poll timer.
 *
 * The socket itself uses the `ws` package here because node has no global
 * WebSocket; the browser uses its native WebSocket via notify.js. Skipped without
 * a live pod.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';

let _session = null;
let _storageRoot = null;

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import { subscribeWebSocket } from './notify.js';
import { podWriteLongChatMessage, ensureProxionContainer } from './pod.js';
import { chatRootUrl, chatDayUrl, dayPath } from './longchat.js';

const LIVE = !!process.env.TEST_CSS_CLIENT_ID;
const TS = new Date().toISOString();
const ROOM = `nt-${Math.random().toString(36).slice(2, 10)}`;
const webId = () => process.env.TEST_WEBID || (_session && _session.info && _session.info.webId) || '';

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
    await podWriteLongChatMessage(ROOM, 'm1', { content: 'seed', from_webid: webId(), timestamp: TS });
}, 90000);

afterAll(async () => {
    if (!LIVE || !_session) return;
    const base = chatRootUrl(_storageRoot, ROOM);
    const d = dayPath(TS);
    const [yy, mm] = d.split('/');
    for (const u of [`${base}${d}/chat.ttl`, `${base}${d}/`, `${base}${yy}/${mm}/`,
        `${base}${yy}/`, `${base}index.ttl`, base]) {
        try { await _session.fetch(u, { method: 'DELETE' }); } catch { /* ignore */ }
    }
    await _session.logout();
}, 60000);

describe.skipIf(!LIVE)('Solid Notifications deliver a day-file change in real time', () => {
    it('discovers + subscribes (v0.3) and gets a receiveFrom socket URL', async () => {
        const receiveFrom = await subscribeWebSocket(chatDayUrl(_storageRoot, ROOM, TS));
        expect(typeof receiveFrom).toBe('string');
        expect(receiveFrom).toMatch(/^wss?:\/\//);
    }, 60000);

    it('pushes a notification when the day file is written externally', async () => {
        const { default: WebSocket } = await import('ws');
        const receiveFrom = await subscribeWebSocket(chatDayUrl(_storageRoot, ROOM, TS));
        expect(receiveFrom).toBeTruthy();
        const sock = new WebSocket(receiveFrom);

        const opened = new Promise((resolve, reject) => {
            sock.on('open', resolve);
            sock.on('error', (e) => reject(e instanceof Error ? e : new Error('ws error')));
        });
        const message = new Promise((resolve) => sock.on('message', (d) => resolve(String(d))));

        try {
            await Promise.race([opened, new Promise((_, r) => setTimeout(() => r(new Error('open timeout')), 15000))]);
            await podWriteLongChatMessage(ROOM, 'm2', {
                content: 'live!', from_webid: webId(), timestamp: new Date().toISOString(),
            });
            const notif = await Promise.race([
                message,
                new Promise((_, r) => setTimeout(() => r(new Error('no notification in time')), 12000)),
            ]);
            expect(/Update|Add/.test(notif)).toBe(true);   // Activity Streams change activity
        } finally {
            try { sock.close(); } catch { /* ignore */ }
        }
    }, 60000);
});
