import { describe, it, expect, vi } from 'vitest';
import { createDmOutboxUi } from './dm-outbox-ui.js';
function fixture() {
    let scope = 'alice', active = true;
    const elements = {};
    const doc = { getElementById: id => elements[id] ||= { innerHTML: '', textContent: '',
        replaceChildren() { this.innerHTML = ''; }, addEventListener(type, fn) { this[type] = fn; } } };
    const outbox = { inspect: vi.fn(async () => [{ id: 'one', peer: '<img src=x>', timestamp: 'yesterday', state: 'waiting', canCancel: true }]),
        cancel: vi.fn(async () => true), send: vi.fn(async () => true) };
    const reviewLegacy = vi.fn();
    const ui = createDmOutboxUi({ doc, outbox, getScope: () => scope, isActive: () => active, reviewLegacy });
    const el = name => doc.getElementById('dm-outbox-' + name);
    const click = (action, id = 'one') => el('rows').click({ target: { closest: () => ({ disabled: false,
        dataset: { outboxAction: action, outboxId: id } }) } });
    return { ui, outbox, el, click, reviewLegacy, switchAccount: () => { scope = 'bob'; }, stop: () => { active = false; } };
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
