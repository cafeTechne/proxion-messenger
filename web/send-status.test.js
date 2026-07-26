// send-status.test.js — R66: optimistic-send failure/retry logic.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createSendStatus } from './send-status.js';

// Minimal message-element stub with a classList + a .msg-content child.
function mkMsgEl() {
    const classes = new Set(['message', 'msg-pending']);
    const children = [];
    const content = {
        appendChild(c) { children.push(c); },
        querySelector: () => null,
    };
    return {
        _children: children,
        classList: {
            add: (c) => classes.add(c),
            remove: (...cs) => cs.forEach(c => classes.delete(c)),
            contains: (c) => classes.has(c),
        },
        querySelector: (sel) => {
            if (sel === '.msg-content') return content;
            if (sel === '.msg-fail-note') return children.find(c => c.className === 'msg-fail-note') || null;
            return null;
        },
    };
}

let els;
beforeEach(() => {
    vi.useFakeTimers();
    els = {};
    global.document = {
        getElementById: (id) => els[id] || null,
        createElement: () => {
            const el = { className: '', textContent: '', type: '', _kids: [], _removed: false,
                appendChild(c) { this._kids.push(c); },
                addEventListener(ev, fn) { this._on = fn; },
                remove() { this._removed = true; },
                querySelector: () => null };
            return el;
        },
    };
});
afterEach(() => { vi.useRealTimers(); });

describe('createSendStatus', () => {
    it('marks a message failed when it is not confirmed in time', () => {
        const ss = createSendStatus();
        els['msg-m1'] = mkMsgEl();
        ss.track('m1', () => {});
        expect(els['msg-m1'].classList.contains('msg-pending')).toBe(true);
        vi.advanceTimersByTime(18000);
        expect(els['msg-m1'].classList.contains('msg-pending')).toBe(false);
        expect(els['msg-m1'].classList.contains('msg-failed')).toBe(true);
        // a fail note was appended
        expect(els['msg-m1']._children.some(c => c.className === 'msg-fail-note')).toBe(true);
    });

    it('does NOT fail a message confirmed before the timeout', () => {
        const ss = createSendStatus();
        els['msg-m1'] = mkMsgEl();
        ss.track('m1', () => {});
        ss.confirm('m1');
        vi.advanceTimersByTime(30000);
        expect(els['msg-m1'].classList.contains('msg-failed')).toBe(false);
        expect(els['msg-m1'].classList.contains('msg-pending')).toBe(false);
    });

    it('confirm is a no-op for an unknown id and never throws', () => {
        const ss = createSendStatus();
        expect(() => ss.confirm('nope')).not.toThrow();
    });

    it('retry re-sends the original payload and restarts the timer', () => {
        const ss = createSendStatus();
        els['msg-m1'] = mkMsgEl();
        const resend = vi.fn();
        ss.track('m1', resend);
        vi.advanceTimersByTime(18000);                 // -> failed
        expect(els['msg-m1'].classList.contains('msg-failed')).toBe(true);
        // click retry
        const note = els['msg-m1']._children.find(c => c.className === 'msg-fail-note');
        const retryBtn = note._kids.find(k => k.className === 'msg-retry-btn');
        retryBtn._on();                                 // simulate click
        expect(resend).toHaveBeenCalledTimes(1);
        expect(els['msg-m1'].classList.contains('msg-pending')).toBe(true);
        expect(els['msg-m1'].classList.contains('msg-failed')).toBe(false);
        // a confirm now clears it cleanly
        ss.confirm('m1');
        vi.advanceTimersByTime(30000);
        expect(els['msg-m1'].classList.contains('msg-failed')).toBe(false);
    });

    it('does not fail a message whose element already lost the pending class', () => {
        const ss = createSendStatus();
        const el = mkMsgEl();
        el.classList.remove('msg-pending');   // e.g. confirmed by a different path
        els['msg-m1'] = el;
        ss.track('m1', () => {});
        vi.advanceTimersByTime(18000);
        expect(el.classList.contains('msg-failed')).toBe(false);
    });
});

describe('trackPodWrite (D2 write-through)', () => {
    const noteOf = (el) => el._children.find(c => c.className === 'msg-pod-note');

    it('shows no note when the pod write succeeds', async () => {
        const ss = createSendStatus();
        els['msg-p1'] = mkMsgEl();
        const ok = await ss.trackPodWrite('p1', async () => true);
        expect(ok).toBe(true);
        expect(noteOf(els['msg-p1'])).toBeUndefined();
    });

    it('shows a "not saved to pod" note with a retry when the write fails', async () => {
        const ss = createSendStatus();
        els['msg-p1'] = mkMsgEl();
        const ok = await ss.trackPodWrite('p1', async () => false);
        expect(ok).toBe(false);
        const note = noteOf(els['msg-p1']);
        expect(note).toBeTruthy();
        expect(note._kids.some(k => k.className === 'msg-retry-btn')).toBe(true);
    });

    it('treats a throwing write as a failure, no unhandled rejection', async () => {
        const ss = createSendStatus();
        els['msg-p1'] = mkMsgEl();
        const ok = await ss.trackPodWrite('p1', async () => { throw new Error('403'); });
        expect(ok).toBe(false);
        expect(noteOf(els['msg-p1'])).toBeTruthy();
    });

    it('retry re-runs the pod write (idempotent gap-fill)', async () => {
        const ss = createSendStatus();
        els['msg-p1'] = mkMsgEl();
        let attempt = 0;
        const writeFn = vi.fn(async () => (++attempt >= 2));   // fail once, then succeed
        await ss.trackPodWrite('p1', writeFn);
        const retryBtn = noteOf(els['msg-p1'])._kids.find(k => k.className === 'msg-retry-btn');
        retryBtn._on();                                        // simulate the retry click
        await Promise.resolve(); await Promise.resolve();      // flush the async re-run
        expect(writeFn).toHaveBeenCalledTimes(2);
    });

    it('is a no-op for a missing element or bad args', async () => {
        const ss = createSendStatus();
        expect(await ss.trackPodWrite('', async () => false)).toBe(false);
        expect(await ss.trackPodWrite('nope', null)).toBe(false);
    });
});
