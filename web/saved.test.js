// saved.js — R59E: bookmark toggle, snapshotting, LRU cap.
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach } from 'vitest';
import {
    toggleSaved, listSaved, removeSaved, snapshotFromMessage, MAX_SAVED,
} from './saved.js';

const msg = (id, over = {}) => ({
    message_id: id, content: 'hello ' + id, from_display_name: 'Alice',
    timestamp: '2026-07-19T00:00:00Z', ...over,
});
const view = { id: 'room-1', type: 'local_room', name: 'general' };

beforeEach(async () => {
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
