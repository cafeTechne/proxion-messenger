import { describe, it, expect, vi } from 'vitest';
import { createDmOutboxUi } from './dm-outbox-ui.js';
function fixture({ reviewUnknown = vi.fn() } = {}) {
    let scope = 'alice', active = true;
    const elements = {};
    const doc = { getElementById: id => elements[id] ||= { innerHTML: '', textContent: '',
        replaceChildren() { this.innerHTML = ''; }, addEventListener(type, fn) { this[type] = fn; } } };
    const outbox = { inspect: vi.fn(async () => [{ id: 'one', peer: '<img src=x>', timestamp: 'yesterday', state: 'waiting', canCancel: true }]),
        cancel: vi.fn(async () => true), send: vi.fn(async () => true) };
    const reviewLegacy = vi.fn();
    const ui = createDmOutboxUi({ doc, outbox, getScope: () => scope, isActive: () => active, reviewLegacy, reviewUnknown });
    const el = name => doc.getElementById('dm-outbox-' + name);
    const click = (action, id = 'one') => el('rows').click({ target: { closest: () => ({ disabled: false,
        dataset: { outboxAction: action, outboxId: id } }) } });
    return { ui, outbox, el, click, reviewLegacy, reviewUnknown, switchAccount: () => { scope = 'bob'; }, stop: () => { active = false; } };
}
describe('pending message review', () => {
    it('escapes names and offers labelled keyboard buttons for safe cancellation', async () => {
        const h = fixture(); await h.ui.refresh();
        expect(h.el('rows').innerHTML).toContain('&lt;img src=x&gt;');
        expect(h.el('rows').innerHTML).toContain('type="button"');
        expect(h.el('rows').innerHTML).toContain('data-outbox-action="cancel"');
        await h.click('cancel'); expect(h.outbox.cancel).toHaveBeenCalledWith('one');
    });
    it('does not offer cancellation of prepared sends or retry to changed recipients', async () => {
        const h = fixture(); h.outbox.inspect.mockResolvedValue([{ id: 'one', peer: 'bob', state: 'attention', canCancel: false }]);
        await h.ui.refresh(); expect(h.el('rows').innerHTML).not.toContain('data-outbox-action="cancel"');
        expect(h.el('rows').innerHTML).toContain(' disabled');
    });
    it('clears a stale view and refuses its actions after account replacement', async () => {
        const h = fixture(); await h.ui.refresh(); h.switchAccount(); await h.click('retry');
        expect(h.outbox.send).not.toHaveBeenCalled(); expect(h.el('rows').innerHTML).toBe('');
    });
    it('ignores delayed inspection from a previous session', async () => {
        const h = fixture(); let finish;
        h.outbox.inspect.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
        const pending = h.ui.refresh(); h.switchAccount(); finish([{ id: 'old', peer: 'private Alice destination' }]); await pending;
        expect(h.el('rows').innerHTML).toBe('');
    });
    it('explains when preparation overtakes a cancellation gesture', async () => {
        const h = fixture(); h.outbox.cancel.mockResolvedValue(false); await h.ui.refresh(); await h.click('cancel');
        expect(h.el('status').textContent).toBe('outbox.cannotCancel');
    });
});

it('links confirmed media to its conversation instead of pretending another upload is needed', async () => {
    const elements = {}, reviewMedia = vi.fn(), outbox = { inspect: async () => [{ id: 'file', peer: 'bob', state: 'media', canCancel: false }] };
    const doc = { getElementById: id => elements[id] ||= { innerHTML: '', textContent: '', replaceChildren() { this.innerHTML = ''; },
        addEventListener(type, callback) { this[type] = callback; } } };
    const ui = createDmOutboxUi({ doc, outbox, getScope: () => 'alice', isActive: () => true, reviewMedia });
    await ui.refresh(); expect(elements['dm-outbox-rows'].innerHTML).toContain('data-outbox-action="media"');
    await elements['dm-outbox-rows'].click({ target: { closest: () => ({ dataset: { outboxAction: 'media', outboxId: 'file' } }) } });
    expect(reviewMedia).toHaveBeenCalledWith('file');
});

it('offers manual review for an older unbound actor without retrying its saved ciphertext', async () => {
    const h = fixture(); h.outbox.inspect.mockResolvedValue([{ id: 'one', peer: 'bob', state: 'unbound', canCancel: false, canRetry: false }]);
    await h.ui.refresh(); expect(h.el('rows').innerHTML).toContain('data-outbox-action="unknown"');
    await h.click('unknown'); expect(h.reviewUnknown).toHaveBeenCalledWith('one'); expect(h.outbox.send).not.toHaveBeenCalled();
});

it('shows labelled, bounded transfer progress with retry and no prepared cancellation', async () => {
    const h = fixture();
    h.outbox.inspect.mockResolvedValue([{ id: 'native:file', peer: 'bob', state: 'nativeUpload',
        canCancel: false, canRetry: true, progress: { received: 3, total: 8 } }]);
    await h.ui.refresh();
    expect(h.el('rows').innerHTML).toContain('<progress value="3" max="8" aria-label="nativeMedia.progressLabel">');
    expect(h.el('rows').innerHTML).toContain('data-outbox-action="retry"');
    expect(h.el('rows').innerHTML).not.toContain('data-outbox-action="cancel"');
    for (const progress of [{ received: '<img src=x>', total: 8 }, { received: 9, total: 8 }, { received: 1, total: 401 }]) {
        h.outbox.inspect.mockResolvedValue([{ id: 'native:file', state: 'nativeUpload', progress }]);
        await h.ui.refresh(); expect(h.el('rows').innerHTML).not.toContain('<progress');
    }
});

it('offers a distinct labelled decline action only for eligible incoming files', async () => {
    const h = fixture(); h.outbox.decline = vi.fn(async () => true);
    h.outbox.inspect.mockResolvedValue([{ id: 'native-in:chunk:file', peer: 'bob', state: 'nativeIncoming', canCancel: false, canDecline: true }]);
    await h.ui.refresh();
    expect(h.el('rows').innerHTML).toContain('data-outbox-action="decline"');
    expect(h.el('rows').innerHTML).not.toContain('data-outbox-action="cancel"');
    await h.click('decline', 'native-in:chunk:file'); expect(h.outbox.decline).toHaveBeenCalledWith('native-in:chunk:file');
    h.outbox.decline.mockResolvedValue(false); await h.click('decline', 'native-in:chunk:file');
    expect(h.el('status').textContent).toBe('nativeMedia.declineUnavailable');
});
