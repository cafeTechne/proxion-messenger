// saved.js — R59E: bookmark toggle, snapshotting, LRU cap.
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Control the account the saved store namespaces its database by, and satisfy
// pod.js's auth imports, without loading the real solid-authn bundle.
const _h = vi.hoisted(() => ({ acct: null, root: null, fetches: [] }));
vi.mock('./auth.js', () => ({
    accountDbName: (base) => (_h.acct ? `${base}::${_h.acct}` : base),
    solidSession: {
        info: { get isLoggedIn() { return !!_h.root; }, webId: null },
        fetch: async (url, opts = {}) => { _h.fetches.push([opts.method || 'GET', url]); return { ok: false, status: 404 }; },
    },
    podStorageRoot: () => _h.root,
}));

import {
    toggleSaved, listSaved, removeSaved, snapshotFromMessage, MAX_SAVED,
    forgetSavedMessage, forgetSavedExpired, upsertSaved, createSaved,
} from './saved.js';

const msg = (id, over = {}) => ({
    message_id: id, content: 'hello ' + id, from_display_name: 'Alice',
    timestamp: '2026-07-19T00:00:00Z', ...over,
});
const view = { id: 'room-1', type: 'local_room', name: 'general' };

beforeEach(async () => {
    _h.acct = null;
    _h.root = null;
    _h.fetches = [];
    for (const r of await listSaved()) await removeSaved(r.id);
});

describe('snapshotFromMessage (pure)', () => {
    it('captures identity, context, and content', () => {
        const s = snapshotFromMessage(msg('m1'), view);
        expect(s.id).toBe('m1');
        expect(s.thread_label).toBe('general');
        expect(s.from_name).toBe('Alice');
        expect(s.content).toBe('hello m1');
        expect(s.has_file).toBe(false);
    });
    it('summarizes attachments instead of storing bytes', () => {
        const s = snapshotFromMessage(msg('m2', { file: { mime_type: 'image/png', data_b64: 'x'.repeat(9000) } }), view);
        expect(s.has_file).toBe(true);
        expect(s.file_kind).toBe('image');
        expect(JSON.stringify(s).length).toBeLessThan(1000);
    });
    it('truncates long content', () => {
        const s = snapshotFromMessage(msg('m3', { content: 'x'.repeat(2000) }), view);
        expect(s.content.length).toBe(500);
    });
});

describe('toggleSaved', () => {
    it('saves then removes on second toggle', async () => {
        expect(await toggleSaved(snapshotFromMessage(msg('m1'), view))).toBe('saved');
        expect((await listSaved()).length).toBe(1);
        expect(await toggleSaved(snapshotFromMessage(msg('m1'), view))).toBe('removed');
        expect(await listSaved()).toEqual([]);
    });
    it('lists most recent first', async () => {
        await toggleSaved({ ...snapshotFromMessage(msg('a'), view), });
        await new Promise(r => setTimeout(r, 5));
        await toggleSaved({ ...snapshotFromMessage(msg('b'), view), });
        const rows = await listSaved();
        expect(rows[0].id).toBe('b');
    });
    it('exports a sane cap', () => {
        expect(MAX_SAVED).toBeGreaterThanOrEqual(100);
    });
});

describe('per-account namespacing (cross-account bleed)', () => {
    const A = 'https://a.example/profile/card#me';
    const B = 'https://b.example/profile/card#me';
    async function clearFor(acct) {
        _h.acct = acct;
        for (const r of await listSaved()) await removeSaved(r.id);
    }
    it('account B never reads account A saved snapshots', async () => {
        await clearFor(A);
        await clearFor(B);
        // Account A bookmarks a message (its snapshot holds decrypted content).
        _h.acct = A;
        await toggleSaved(snapshotFromMessage(msg('m1', { content: 'A secret' }), view));
        expect((await listSaved()).map((r) => r.content)).toEqual(['A secret']);
        // Account B must see none of A's saved rows.
        _h.acct = B;
        expect(await listSaved()).toEqual([]);
        await toggleSaved(snapshotFromMessage(msg('m2', { content: 'B note' }), view));
        expect((await listSaved()).map((r) => r.content)).toEqual(['B note']);
        // A's store is untouched.
        _h.acct = A;
        expect((await listSaved()).map((r) => r.content)).toEqual(['A secret']);
        await clearFor(A);
        await clearFor(B);
        _h.acct = null;
    });
});

describe('bookmarks follow message deletion and expiry', () => {
    const podDeletes = () => _h.fetches.filter(([m]) => m === 'DELETE').map(([, u]) => u);

    it('a deleted message loses its bookmark, locally and on the pod', async () => {
        await toggleSaved(snapshotFromMessage(msg('gone'), view));
        await toggleSaved(snapshotFromMessage(msg('kept'), view));
        _h.root = 'https://pod.example/alice/';
        expect(await forgetSavedMessage('gone')).toBe(true);
        expect((await listSaved()).map((r) => r.id)).toEqual(['kept']);
        expect(podDeletes()).toContain('https://pod.example/alice/proxion/saved/gone.jsonld');
    });

    it('an unsaved message is a no-op with no pod request', async () => {
        _h.root = 'https://pod.example/alice/';
        expect(await forgetSavedMessage('never-saved')).toBe(false);
        expect(_h.fetches).toEqual([]);
    });

    it('expiry drops bookmarks of expired messages in that thread only', async () => {
        const at = (id, ts, thread = 'room-1') =>
            toggleSaved(snapshotFromMessage(msg(id, { timestamp: ts }), { ...view, id: thread }));
        await at('before-timer', '2026-01-01T00:00:00+00:00');
        await at('expired', '2026-02-01T00:00:00+00:00');
        await at('fresh', '2026-04-01T00:00:00+00:00');
        await at('other-thread', '2026-02-01T00:00:00+00:00', 'room-2');
        await at('alias', '2026-02-01T00:00:00+00:00', 'did:key:zPeer');
        _h.root = 'https://pod.example/alice/';
        const n = await forgetSavedExpired(['room-1', 'did:key:zPeer', undefined],
            '2026-03-01T00:00:00+00:00', '2026-01-15T00:00:00+00:00');
        expect(n).toBe(2);
        expect((await listSaved()).map((r) => r.id).sort())
            .toEqual(['before-timer', 'fresh', 'other-thread']);
        expect(podDeletes().sort()).toEqual([
            'https://pod.example/alice/proxion/saved/alias.jsonld',
            'https://pod.example/alice/proxion/saved/expired.jsonld',
        ]);
    });
});

describe('main.js wiring', () => {
    it('message_deleted and dm_messages_expired drop bookmarks', async () => {
        const { readFileSync } = await import('node:fs');
        const src = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
        const block = (name) => {
            const start = src.indexOf(`case "${name}": {`);
            expect(start).toBeGreaterThan(-1);
            return src.slice(start, src.indexOf('break;', start));
        };
        expect(block('message_deleted')).toContain('forgetSavedMessage(event.message_id)');
        expect(block('dm_messages_expired')).toContain('forgetSavedExpired(');
    });
});

describe('Saved account-bound transactions and reference UI', () => {
    it('does not return old-account snapshots when an IndexedDB read completes after a switch', async () => {
        const a = crypto.randomUUID(), b = crypto.randomUUID(); _h.acct = a;
        await toggleSaved(snapshotFromMessage(msg('private'), view));
        const getAll = IDBObjectStore.prototype.getAll;
        const spy = vi.spyOn(IDBObjectStore.prototype, 'getAll').mockImplementation(function (...args) {
            const request = getAll.apply(this, args); request.addEventListener('success', () => { _h.acct = b; }, { once: true }); return request;
        });
        try { expect(await listSaved()).toEqual([]); } finally { spy.mockRestore(); }
        expect(await listSaved()).toEqual([]);
        _h.acct = a; expect((await listSaved())[0].id).toBe('private');
    });
    it('aborts deletion on account replacement without deleting either account copy', async () => {
        const a = crypto.randomUUID(), b = crypto.randomUUID();
        for (const account of [a, b]) { _h.acct = account; await toggleSaved(snapshotFromMessage(msg('same'), view)); }
        _h.acct = a; const get = IDBObjectStore.prototype.get;
        const spy = vi.spyOn(IDBObjectStore.prototype, 'get').mockImplementation(function (...args) {
            const request = get.apply(this, args); request.addEventListener('success', () => { _h.acct = b; }, { once: true }); return request;
        });
        try { await expect(forgetSavedMessage('same')).rejects.toThrow(); } finally { spy.mockRestore(); }
        for (const account of [a, b]) { _h.acct = account; expect((await listSaved()).map(row => row.id)).toEqual(['same']); }
    });
    it('keeps imported references bounded and strips unrequested snapshot bytes', async () => {
        _h.acct = crypto.randomUUID();
        for (let n = 0; n <= MAX_SAVED; n++) await upsertSaved({ id: `saved-${n}`, thread_id: 'thread', content: 'x'.repeat(600), data_b64: 'private file bytes' });
        const rows = await listSaved(); expect(rows).toHaveLength(MAX_SAVED);
        expect(rows.every(row => row.content.length === 500 && row.data_b64 === undefined)).toBe(true);
    });
    it('carries original conversation/type/target into Jump and rejects an old-account button', async () => {
        _h.acct = crypto.randomUUID();
        await toggleSaved({ ...snapshotFromMessage(msg('ref'), view), reference_target: 'https://pod.example/room/day#ref' });
        const element = () => ({ style: {}, dataset: {}, children: [], listeners: {}, appendChild(row) { this.children.push(row); },
            addEventListener(type, fn) { this.listeners[type] = fn; }, set innerHTML(value) { this.html = value; this.children = []; }, get innerHTML() { return this.html || ''; } });
        const list = element(), panel = element(); panel.style.display = 'block';
        global.document = { getElementById: id => id === 'saved-list' ? list : panel, createElement: element };
        const navigateReference = vi.fn(async () => true), controller = createSaved({ showToast: vi.fn(), navigateReference });
        await controller.renderSaved();
        const jump = list.children[0].children[2].children[0]; await jump.listeners.click();
        expect(navigateReference).toHaveBeenCalledWith(expect.objectContaining({ message_id: 'ref', thread_id: 'room-1', thread_type: 'local_room', reference_target: 'https://pod.example/room/day#ref' }));
        _h.acct = crypto.randomUUID(); await jump.listeners.click();
        expect(navigateReference).toHaveBeenCalledOnce(); expect(panel.style.display).toBe('none'); expect(list.children).toEqual([]);
    });
});
