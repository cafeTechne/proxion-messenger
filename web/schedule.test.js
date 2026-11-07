import { describe, it, expect, vi } from 'vitest';
import { createScheduledComposer } from './schedule.js';

const command = { thread_id: 'room-a', content: 'Hello', send_at: '2026-11-07T12:00:00+00:00' };
function harness(send = vi.fn(() => true)) {
    let draft = ' Hello ', thread = 'room-a', timeout;
    const callbacks = { onPending: vi.fn(), onFailure: vi.fn(), onConfirmed: vi.fn() };
    const composer = createScheduledComposer({
        ...callbacks, send, getDraft: () => draft, getThreadId: () => thread,
        clearDraft: () => { draft = ''; },
        setTimer: (fn) => { timeout = fn; return 1; }, clearTimer: vi.fn(),
    });
    return { composer, send, ...callbacks, draft: () => draft,
        type: (text) => { draft = text; }, switchThread: (id) => { thread = id; },
        timeout: () => timeout(),
        ack: (extra = {}) => composer.handleEvent({ type: 'message_scheduled', ...command, ...extra }),
    };
}

describe('scheduled composer acknowledgement', () => {
    it('retains the draft while pending and clears it only after the matching acknowledgement', () => {
        const h = harness();
        expect(h.composer.submit(command)).toBe(true);
        expect(h.draft()).toBe(' Hello ');
        expect(h.composer.isPending()).toBe(true);
        expect(h.ack()).toBe(true);
        expect(h.draft()).toBe('');
        expect(h.composer.isPending()).toBe(false);
        expect(h.onConfirmed).toHaveBeenCalledOnce();
    });
    it.each(['offline', 'throw'])('retains the draft when send fails: %s', (failure) => {
        const h = harness(() => { if (failure === 'throw') throw new Error('closed'); return false; });
        expect(h.composer.submit(command)).toBe(false);
        expect(h.draft()).toBe(' Hello ');
        expect(h.composer.isPending()).toBe(false);
        expect(h.onFailure).toHaveBeenCalledWith('offline');
    });
    it('handles an immediate acknowledgement without losing pending state or sending twice', () => {
        let h;
        h = harness(() => { h.ack(); return true; });
        expect(h.composer.submit(command)).toBe(true);
        expect(h.composer.isPending()).toBe(false);
        expect(h.draft()).toBe('');
    });
    it('prevents duplicate submissions while waiting for an acknowledgement', () => {
        const h = harness();
        h.composer.submit(command);
        expect(h.composer.submit(command)).toBe(false);
        expect(h.send).toHaveBeenCalledOnce();
    });
    it('preserves edits made while waiting and ignores acknowledgements for another request', () => {
        const h = harness();
        h.composer.submit(command);
        expect(h.ack({ thread_id: 'room-b' })).toBe(false);
        expect(h.ack({ send_at: '2026-11-08T12:00:00+00:00' })).toBe(false);
        h.type('Another message');
        h.ack();
        expect(h.draft()).toBe('Another message');
    });
    it('never clears a different conversation even if it has identical draft text', () => {
        const h = harness();
        h.composer.submit(command);
        h.switchThread('room-b');
        h.ack();
        expect(h.draft()).toBe(' Hello ');
    });
    it('retains the draft and enables retry on a correlated gateway rejection', () => {
        const h = harness();
        h.composer.submit(command);
        expect(h.composer.handleEvent({ type: 'error', cmd: 'schedule_message', thread_id: 'room-b' })).toBe(false);
        expect(h.composer.handleEvent({ type: 'error', cmd: 'schedule_message', thread_id: 'room-a' })).toBe(true);
        expect(h.draft()).toBe(' Hello ');
        expect(h.composer.submit(command)).toBe(true);
    });
    it('keeps drafts on timeout and on a late acknowledgement after timeout', () => {
        const h = harness();
        h.composer.submit(command);
        h.timeout();
        expect(h.composer.isPending()).toBe(false);
        expect(h.onFailure).toHaveBeenCalledWith('unconfirmed');
        expect(h.ack()).toBe(false);
        expect(h.draft()).toBe(' Hello ');
    });
});
