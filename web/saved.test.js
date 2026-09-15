// saved.js — R59E: bookmark toggle, snapshotting, LRU cap.
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Control the account the saved store namespaces its database by, and satisfy
// pod.js's auth imports, without loading the real solid-authn bundle.
const _h = vi.hoisted(() => ({ acct: null }));
vi.mock('./auth.js', () => ({
    accountDbName: (base) => (_h.acct ? `${base}::${_h.acct}` : base),
    solidSession: { info: { isLoggedIn: false, webId: null }, fetch: async () => ({ ok: false, status: 404 }) },
    podStorageRoot: () => null,
}));

import {
    toggleSaved, listSaved, removeSaved, snapshotFromMessage, MAX_SAVED,
} from './saved.js';

const msg = (id, over = {}) => ({
    message_id: id, content: 'hello ' + id, from_display_name: 'Alice',
    timestamp: '2026-07-19T00:00:00Z', ...over,
});
const view = { id: 'room-1', type: 'local_room', name: 'general' };

beforeEach(async () => {
    _h.acct = null;
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
