import { describe, it, expect, vi } from 'vitest';
import { createRoomMedia } from './room-media.js';
function fixture({ postFails = false } = {}) {
    const rows = new Map(), resources = new Map(), calls = [], onMessage = vi.fn(), showToast = vi.fn();
    let active = true, handler;
    const panel = { hidden: true, innerHTML: '', replaceChildren() { this.innerHTML = ''; }, addEventListener(_, fn) { handler = fn; } };
    const ctx = { actor: 'https://pod.example/me#id', roomId: 'room1', container: 'https://pod.example/proxion/rooms/room1/', senderName: 'Alice', current: () => active,
        fetch: async (url, options = {}) => {
            calls.push(options.method || 'GET');
            if (options.method === 'POST') {
                const location = ctx.container + options.headers.Slug; resources.set(location, options.body);
                return new Response('', { status: 201, headers: { Location: location } });
            }
            return new Response(resources.get(url), { status: 200 });
        } };
    const store = { list: async actor => [...rows.values()].filter(row => row.actor === actor),
        put: async row => rows.set(row.key, structuredClone(row)), remove: async key => rows.delete(key) };
    const post = vi.fn(async row => ({ ok: !postFails, message: { content: row.content } }));
    const options = { getContext: () => ctx, post, store, onMessage, showToast, doc: { getElementById: () => panel } };
    const controller = createRoomMedia(options);
    return { controller, rows, calls, options, panel, post, onMessage, showToast, ctx,
        offline: () => { active = false; }, restorePost: () => { postFails = false; },
        retry: key => handler({ target: { closest: selector => selector.includes('retry') ? { dataset: { roomMediaRetry: key } } : null } }) };
}
describe('durable room-media drafts', () => {
    it('keeps an unconfirmed message and resumes after controller recreation without uploading again', async () => {
        const h = fixture({ postFails: true });
        await h.controller.send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        expect(h.rows.size).toBe(1); expect(h.onMessage).not.toHaveBeenCalled();
        const [key, draft] = [...h.rows.entries()][0];
        expect(draft.content).toContain('proxion-attachment'); expect(draft.senderName).toBe('Alice');
        const uploads = h.calls.filter(method => method === 'POST').length;
        h.restorePost(); createRoomMedia(h.options); await h.retry(key);
        expect(h.rows.size).toBe(0); expect(h.onMessage).toHaveBeenCalledOnce();
        expect(h.calls.filter(method => method === 'POST')).toHaveLength(uploads);
        expect(h.post.mock.calls[1][0].id).toBe(draft.id);
    });
    it('does not send when durable draft storage fails', async () => {
        const h = fixture(); h.options.store.put = async () => { throw Error('quota'); };
        await h.controller.send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        expect(h.calls).toHaveLength(0); expect(h.post).not.toHaveBeenCalled();
        expect(h.showToast).toHaveBeenCalledWith('roomMedia.storageFailed', 'error');
    });
    it('does not retry another account or room draft', async () => {
        const h = fixture({ postFails: true });
        await h.controller.send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        const key = [...h.rows.keys()][0]; h.ctx.actor = 'https://other.example/#me';
        await h.retry(key); expect(h.post).toHaveBeenCalledOnce();
        expect(h.rows.size).toBe(1);
    });
});
