import { describe, it, expect, vi } from 'vitest';
import { createAssetInventoryUi } from './asset-inventory-ui.js';
const root = 'https://pod.example/alice/', base = root + 'proxion/rooms/';
const listing = (url, children) => JSON.stringify({ '@id': url, '@type': 'http://www.w3.org/ns/ldp#BasicContainer',
    'http://www.w3.org/ns/ldp#contains': children.map(child => ({ '@id': child })) });
function fixture() {
    let actor = 'alice', open = true;
    const elements = {};
    const doc = { getElementById: id => elements[id] ||= { hidden: false, disabled: false, innerHTML: '', textContent: '', value: '',
        replaceChildren() { this.innerHTML = ''; this.textContent = ''; }, addEventListener(type, fn) { this[type] = fn; } } };
    const fetch = vi.fn(async (url, options) => {
        if (options.method === 'HEAD') return new Response(null, { headers: { 'content-length': '12' } });
        if (url === base) return new Response(listing(base, ['room/']));
        if (url === base + 'room/') return new Response(listing(url, ['asset-a-0.json', 'files/']));
        return new Response(listing(url, []));
    });
    const ui = createAssetInventoryUi({ doc, getRoomName: () => '<img src=x>', getReferences: () => [base + 'room/asset-a-0.json'],
        getContext: () => {
            const captured = actor;
            return open ? { actor, root, fetch, current: () => open && actor === captured } : null;
        } });
    return { ui, fetch, elements, el: id => doc.getElementById('asset-inventory-' + id), switchAccount: () => { actor = 'bob'; }, close: () => { open = false; ui.reset(); } };
}
describe('stored room file review controls', () => {
    it('renders escaped names and honest reference states with no destructive actions', async () => {
        const h = fixture(); await h.ui.refresh();
        expect(h.el('rooms').innerHTML).toContain('&lt;img src=x&gt;');
        expect(h.el('results').innerHTML).toContain('asset-a-0.json');
        expect(h.el('results').innerHTML).toContain('inventory.referenced');
        expect(h.el('results').innerHTML).toContain('data-asset-inventory-path="files/"');
        expect(h.el('results').innerHTML).not.toContain('data-delete');
        expect(h.fetch.mock.calls.every(([, options]) => !options.method || options.method === 'HEAD')).toBe(true);
    });
    it('opens legacy folders and returns to the selected room', async () => {
        const h = fixture(); await h.ui.refresh();
        h.el('results').click({ target: { closest: () => ({ dataset: { assetInventoryPath: 'files/' } }) } });
        await vi.waitFor(() => expect(h.el('path').textContent).toBe('room/files/'));
        expect(h.el('up').disabled).toBe(false);
        h.el('up').click();
        await vi.waitFor(() => expect(h.el('path').textContent).toBe('room/'));
    });
    it('ignores late results after account changes and aborts requests on close', async () => {
        const h = fixture(); let finish;
        h.fetch.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
        const pending = h.ui.refresh();
        const signal = h.fetch.mock.calls[0][1].signal;
        h.switchAccount(); h.ui.sync();
        expect(signal.aborted).toBe(true);
        finish(new Response(listing(base, ['old-room/']))); await pending;
        expect(h.el('results').innerHTML).toBe(''); expect(h.el('rooms').innerHTML).toBe('');
        await h.ui.refresh(); h.close();
        expect(h.el('results').innerHTML).toBe('');
    });
    it('shows a failure rather than an empty-success state when listing is denied', async () => {
        const h = fixture(); h.fetch.mockResolvedValue(new Response(null, { status: 403 }));
        await h.ui.refresh();
        expect(h.el('status').textContent).toBe('inventory.failed');
        expect(h.el('browser').hidden).toBe(true);
    });
});
