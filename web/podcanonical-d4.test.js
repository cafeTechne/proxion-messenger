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

import { podWriteLongChatMessage, podReadLongChatRecent, ensureProxionContainer } from './pod.js';
import { chatRootUrl, chatDayUrl, messageIriFor, dayPath, NS } from './longchat.js';

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
    // Two devices, one day file. Intended order A then B; clocks disagree.
    await podWriteLongChatMessage(ROOM, 'msgA', { content: 'A intended first', from_webid: me, timestamp: A_TIME });
    await podWriteLongChatMessage(ROOM, 'msgB', { content: 'B intended second', from_webid: me, timestamp: B_TIME });

    // The remedy under test: a monotonic px:seq hint in the SAME day file, written
    // the way D4's build would (A=1, B=2), independent of the clocks.
    const day = chatDayUrl(_storageRoot, ROOM, A_TIME);   // A and B share the UTC day
    const seqPatch = `INSERT DATA {\n` +
        `  <${messageIriFor(_storageRoot, ROOM, 'msgA', A_TIME)}> <${NS.px}seq> 1 .\n` +
        `  <${messageIriFor(_storageRoot, ROOM, 'msgB', B_TIME)}> <${NS.px}seq> 2 .\n}`;
    const res = await _session.fetch(day, {
        method: 'PATCH', headers: { 'Content-Type': 'application/sparql-update' }, body: seqPatch,
    });
    if (!res.ok) throw new Error(`seq PATCH failed: ${res.status}`);
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

describe.skipIf(!LIVE)('D4 prototype: multi-device ordering under clock skew', () => {
    it('THE PROBLEM: timestamp order reverses the intended order under skew', async () => {
        const byTime = await podReadLongChatRecent(ROOM, WINDOW);
        // podReadLongChatRecent orders by dct:created, so the behind-clock B sorts
        // before A even though A was intended (and delivered) first. This is why
        // pure timestamp order is not enough for multi-device.
        expect(byTime.map(m => m.message_id)).toEqual(['msgB', 'msgA']);   // reversed vs intent
    }, 60000);

    it('THE REMEDY: a monotonic px:seq recovers the intended order regardless of clocks', async () => {
        // Read the raw day file and order by the seq hint instead of the clock.
        const day = chatDayUrl(_storageRoot, ROOM, A_TIME);
        const res = await _session.fetch(day, { headers: { Accept: 'application/ld+json' } });
        const json = await res.json();
        const nodes = Array.isArray(json) ? json : (json['@graph'] || [json]);
        const seqOf = (node) => {
            const v = node[`${NS.px}seq`];
            const raw = Array.isArray(v) ? v[0] : v;
            const n = raw && typeof raw === 'object' ? raw['@value'] : raw;
            return n == null ? null : Number(n);
        };
        const withSeq = nodes
            .filter(n => seqOf(n) != null)
            .map(n => ({ id: String(n['@id']).split('#').pop(), seq: seqOf(n) }))
            .sort((a, b) => a.seq - b.seq);
        expect(withSeq.map(x => x.id)).toEqual(['msgA', 'msgB']);   // intended order recovered
    }, 60000);
});
