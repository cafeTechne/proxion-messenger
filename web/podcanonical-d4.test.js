/**
 * PLAN_ROUND_69 D4 — prototype multi-device ordering BEFORE building it.
 *
 * The framing says the gateway owns live ORDER while the pod owns durable
 * CONTENT. When two devices append to one day file, the pod carries only each
 * message's client timestamp (dct:created). If device clocks are skewed, the
 * pod's timestamp order can differ from the order members actually saw live. The
 * question this answers against a real pod: is pure timestamp order enough, or is
 * a per-message monotonic hint needed?
 *
 * Finding (asserted below): timestamp order MISORDERS under clock skew, and a
 * monotonic px:seq hint written into the day file recovers the intended order
 * regardless of the clocks. So D4's build should add a seq hint to the write path
 * and prefer it on read. This is a throwaway prototype that doubles as a guard;
 * it does not change the product.
 *
 * Run (skipped without a live pod): see podcanonical-prototype.test.js header.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';

let _session = null;
let _storageRoot = null;

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import {
    podWriteLongChatMessage, podReadLongChatRecent, podSetLongChatSeq, ensureProxionContainer,
} from './pod.js';
import { chatRootUrl, dayPath } from './longchat.js';

const LIVE = !!process.env.TEST_CSS_CLIENT_ID;
const DAY = new Date().toISOString().slice(0, 10);
// A is intended FIRST but its device clock runs AHEAD; B is intended SECOND but
// its clock is BEHIND. So dct:created order (B, A) is the reverse of intent (A, B).
const A_TIME = `${DAY}T09:05:00.000Z`;
const B_TIME = `${DAY}T09:01:00.000Z`;
const WINDOW = 2;
const ROOM = `d4-${Math.random().toString(36).slice(2, 10)}`;

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
    // Two of the same user's devices, one day file. Intended order A then B, but A
    // was written with the LATER client clock (skew), so dct:created order reverses.
    await podWriteLongChatMessage(ROOM, 'msgA', { content: 'A intended first', from_webid: me, timestamp: A_TIME });
    await podWriteLongChatMessage(ROOM, 'msgB', { content: 'B intended second', from_webid: me, timestamp: B_TIME });
}, 90000);

afterAll(async () => {
    if (!LIVE || !_session) return;
    const base = chatRootUrl(_storageRoot, ROOM);
    const d = dayPath(A_TIME);
    const [yy, mm] = d.split('/');
    for (const u of [`${base}${d}/chat.ttl`, `${base}${d}/`, `${base}${yy}/${mm}/`,
        `${base}${yy}/`, `${base}index.ttl`, base]) {
        try { await _session.fetch(u, { method: 'DELETE' }); } catch { /* ignore */ }
    }
    await _session.logout();
}, 60000);

describe.skipIf(!LIVE)('D4: multi-device ordering under clock skew, via the real path', () => {
    it('THE PROBLEM: timestamp order reverses the intended order under skew', async () => {
        const byTime = await podReadLongChatRecent(ROOM, WINDOW);
        // podReadLongChatRecent falls back to dct:created when no seq is present, so
        // the behind-clock B sorts before A even though A was intended (and
        // delivered) first. This is why pure timestamp order is not enough.
        expect(byTime.map(m => m.message_id)).toEqual(['msgB', 'msgA']);   // reversed vs intent
    }, 60000);

    it('THE REMEDY: stamping px:seq via the product path recovers the intended order', async () => {
        // Exactly what the echo handler does: stamp the gateway's server-clock order
        // (A earlier -> smaller seq, B later -> larger seq) through podSetLongChatSeq.
        expect(await podSetLongChatSeq(ROOM, 'msgA', A_TIME, 1000)).toBe(true);
        expect(await podSetLongChatSeq(ROOM, 'msgB', B_TIME, 2000)).toBe(true);

        // Now the real read orders by seq (compareByOrder), regardless of the clocks.
        const bySeq = await podReadLongChatRecent(ROOM, WINDOW);
        expect(bySeq.map(m => m.message_id)).toEqual(['msgA', 'msgB']);   // intended order recovered
        expect(bySeq.find(m => m.message_id === 'msgA').seq).toBe(1000);
        expect(bySeq.find(m => m.message_id === 'msgB').seq).toBe(2000);
    }, 60000);
});
