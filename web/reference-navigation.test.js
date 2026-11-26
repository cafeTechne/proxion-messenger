import { describe, expect, it, vi } from 'vitest';
import { createReferenceNavigation } from './reference-navigation.js';
const reference = { thread_id: 'a', thread_type: 'dm', message_id: 'native:did:key:alice:older' };
function harness() {
    let scope = 'account-a', view = { id: 'b', type: 'dm' }, revision = 0;
    const message = { message_id: reference.message_id, thread_id: 'a', file: { data_b64: 'eA==' } };
    const options = { getScope: () => scope, getView: () => view, getViewRevision: () => revision,
        findConversation: vi.fn(ref => ({ id: ref.thread_id, type: 'dm' })),
        openConversation: vi.fn(target => { view = { ...target }; revision++; return true; }),
        resolveMessage: vi.fn(async () => message), revealMessage: vi.fn(async () => ({ dataset: { messageId: reference.message_id } })),
        focusMessage: vi.fn(() => true), onUnavailable: vi.fn() };
    return { options, message, navigator: createReferenceNavigation(options), account: () => { scope = 'account-b'; },
        view: next => { view = next; revision++; } };
}
describe('guarded retained reference navigation', () => {
    it('opens the original conversation before loading and focuses its exact retained message', async () => {
        const h = harness(); h.options.resolveMessage.mockImplementationOnce(async (_ref, view, current) => {
            expect(view).toEqual({ id: 'a', type: 'dm' }); expect(current()).toBe(true); return h.message;
        });
        expect(await h.navigator.navigate(reference)).toBe(true); expect(h.options.openConversation).toHaveBeenCalledOnce();
        expect(h.options.focusMessage).toHaveBeenCalledWith(expect.objectContaining({ dataset: { messageId: reference.message_id } }));
    });
    it.each(['missing conversation', 'wrong type', 'wrong message', 'wrong thread', 'deleted', 'missing element'])('fails explicitly without inventing a destination: %s', async condition => {
        const h = harness();
        if (condition === 'missing conversation') h.options.findConversation.mockReturnValue(null);
        if (condition === 'wrong type') h.options.findConversation.mockReturnValue({ id: 'a', type: 'room' });
        if (condition === 'wrong message') h.options.resolveMessage.mockResolvedValue({ ...h.message, message_id: 'other' });
        if (condition === 'wrong thread') h.options.resolveMessage.mockResolvedValue({ ...h.message, thread_id: 'b' });
        if (condition === 'deleted') h.options.resolveMessage.mockResolvedValue({ ...h.message, deleted: true });
        if (condition === 'missing element') h.options.revealMessage.mockResolvedValue(null);
        expect(await h.navigator.navigate(reference)).toBe(false); expect(h.options.focusMessage).not.toHaveBeenCalled(); expect(h.options.onUnavailable).toHaveBeenCalledOnce();
    });
    it('aborts on account change or view replacement, including leaving and returning to the same view', async () => {
        for (const change of ['account', 'view']) {
            const h = harness(); h.options.resolveMessage.mockImplementationOnce(async () => {
                if (change === 'account') h.account(); else { h.view({ id: 'b', type: 'dm' }); h.view({ id: 'a', type: 'dm' }); }
                return h.message;
            });
            expect(await h.navigator.navigate(reference)).toBe(false); expect(h.options.revealMessage).not.toHaveBeenCalled(); expect(h.options.onUnavailable).not.toHaveBeenCalled();
        }
    });
    it('cancels superseded navigation and stale asynchronous reveal without moving focus', async () => {
        const h = harness(); h.options.revealMessage.mockImplementationOnce(async () => { h.navigator.cancel(); return { dataset: { messageId: reference.message_id } }; });
        expect(await h.navigator.navigate(reference)).toBe(false); expect(h.options.focusMessage).not.toHaveBeenCalled();
    });
    it('aborts if the original conversation disappears while its retained message loads', async () => {
        const h = harness(); h.options.resolveMessage.mockImplementationOnce(async () => {
            h.options.findConversation.mockReturnValue(null); return h.message;
        });
        expect(await h.navigator.navigate(reference)).toBe(false); expect(h.options.revealMessage).not.toHaveBeenCalled(); expect(h.options.onUnavailable).not.toHaveBeenCalled();
    });

});
