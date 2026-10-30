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
            if (sel === '.msg-wait-note') return children.find(c => c.className === 'msg-wait-note' && !c._removed) || null;
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

describe('createSendStatus while offline', () => {
    const waitNote = (el) => el._children.find(c => c.className === 'msg-wait-note' && !c._removed);

    it('does not fail a message queued while the socket is not open', () => {
        let online = false;
        const ss = createSendStatus({ isOnline: () => online });
        els['msg-q1'] = mkMsgEl();
        ss.track('q1', () => {});
        vi.advanceTimersByTime(60000);
        expect(els['msg-q1'].classList.contains('msg-failed')).toBe(false);
        expect(els['msg-q1'].classList.contains('msg-pending')).toBe(true);
        expect(waitNote(els['msg-q1']).textContent).toBeTruthy();
    });

    it('starts the confirm timer once the queue flushes', () => {
        let online = false;
        const ss = createSendStatus({ isOnline: () => online });
        els['msg-q1'] = mkMsgEl();
        ss.track('q1', () => {});
        online = true;
        ss.resumeWaiting();
        expect(waitNote(els['msg-q1'])).toBeUndefined();
        vi.advanceTimersByTime(17999);
        expect(els['msg-q1'].classList.contains('msg-failed')).toBe(false);
        vi.advanceTimersByTime(1);
        expect(els['msg-q1'].classList.contains('msg-failed')).toBe(true);
    });

    it('a confirmed queued message clears the waiting note and never fails', () => {
        let online = false;
        const ss = createSendStatus({ isOnline: () => online });
        els['msg-q1'] = mkMsgEl();
        ss.track('q1', () => {});
        online = true;
        ss.resumeWaiting();
        ss.confirm('q1');
        vi.advanceTimersByTime(60000);
        expect(els['msg-q1'].classList.contains('msg-failed')).toBe(false);
        expect(waitNote(els['msg-q1'])).toBeUndefined();
    });

    it('resumeWaiting leaves messages sent while online alone', () => {
        const ss = createSendStatus({ isOnline: () => true });
        els['msg-o1'] = mkMsgEl();
        ss.track('o1', () => {});
        vi.advanceTimersByTime(10000);
        ss.resumeWaiting();               // must not restart the running timer
        vi.advanceTimersByTime(8000);
        expect(els['msg-o1'].classList.contains('msg-failed')).toBe(true);
    });
});

describe('refused sends (rate limit, error frames)', () => {
    it('retryLater resends after the delay and keeps the bubble pending', () => {
        const ss = createSendStatus({ isOnline: () => true });
        els['msg-r1'] = mkMsgEl();
        const resend = vi.fn();
        ss.track('r1', resend);
        ss.retryLater('r1', 2000);
        vi.advanceTimersByTime(1999);
        expect(resend).not.toHaveBeenCalled();
        vi.advanceTimersByTime(1);
        expect(resend).toHaveBeenCalledTimes(1);
        expect(els['msg-r1'].classList.contains('msg-failed')).toBe(false);
        ss.confirm('r1');
        vi.advanceTimersByTime(60000);
        expect(els['msg-r1'].classList.contains('msg-failed')).toBe(false);
    });

    it('fails after repeated refusals instead of retrying forever', () => {
        const ss = createSendStatus({ isOnline: () => true });
        els['msg-r2'] = mkMsgEl();
        const resend = vi.fn();
        ss.track('r2', resend);
        for (let i = 0; i < 3; i++) { ss.retryLater('r2', 1000); vi.advanceTimersByTime(1000); }
        expect(resend).toHaveBeenCalledTimes(3);
        ss.retryLater('r2', 1000);
        expect(els['msg-r2'].classList.contains('msg-failed')).toBe(true);
    });

    it('fail() marks the bubble at once, without waiting for the timeout', () => {
        const ss = createSendStatus({ isOnline: () => true });
        els['msg-r3'] = mkMsgEl();
        ss.track('r3', () => {});
        ss.fail('r3');
        expect(els['msg-r3'].classList.contains('msg-failed')).toBe(true);
    });

    it('Retry checks the connection before resending', () => {
        const order = [];
        const ss = createSendStatus({ isOnline: () => true, onRetry: () => order.push('probe') });
        els['msg-r4'] = mkMsgEl();
        ss.track('r4', () => order.push('resend'));
        ss.fail('r4');
        const note = els['msg-r4']._children.find(c => c.className === 'msg-fail-note');
        note._kids[1]._on();
        expect(order).toEqual(['probe', 'resend']);
    });
});

// The state outlives the bubble: switching threads or reloading redraws the
// feed, and applyTo paints the redrawn bubble from it.
describe('state across a redraw', () => {
    const failNote = (el) => el._children.find(c => c.className === 'msg-fail-note');
    const podNote = (el) => el._children.find(c => c.className === 'msg-pod-note' && !c._removed);
    const fresh = () => { const el = mkMsgEl(); el.classList.remove('msg-pending'); return el; };

    it('a send that fails while another thread is open shows failed when redrawn', () => {
        const states = [];
        const ss = createSendStatus({ onState: (id, st) => states.push([id, st]) });
        els['msg-a1'] = mkMsgEl();
        ss.track('a1', () => {});
        delete els['msg-a1'];                 // thread switched away
        vi.advanceTimersByTime(18000);
        const el = fresh();
        els['msg-a1'] = el;
        ss.applyTo(el, { message_id: 'a1' });
        expect(el.classList.contains('msg-failed')).toBe(true);
        expect(failNote(el)).toBeTruthy();
        expect(states).toEqual([['a1', 'pending'], ['a1', 'failed']]);
    });

    it('a pending send stays pending when redrawn', () => {
        const ss = createSendStatus();
        els['msg-a2'] = mkMsgEl();
        ss.track('a2', () => {});
        const el = fresh();
        els['msg-a2'] = el;
        ss.applyTo(el, { message_id: 'a2' });
        expect(el.classList.contains('msg-pending')).toBe(true);
    });

    it('a stored undelivered record is shown failed, and Retry rebuilds the resend', () => {
        const resend = vi.fn();
        const ss = createSendStatus({ resendFor: (id) => (id === 'a3' ? resend : null) });
        const el = fresh();
        els['msg-a3'] = el;
        ss.applyTo(el, { message_id: 'a3', delivery: 'pending' });
        expect(el.classList.contains('msg-failed')).toBe(true);
        failNote(el)._kids[1]._on();
        expect(resend).toHaveBeenCalledTimes(1);
        expect(el.classList.contains('msg-pending')).toBe(true);
        ss.confirm('a3');
        const again = fresh();
        els['msg-a3'] = again;
        ss.applyTo(again, { message_id: 'a3', delivery: 'pending' });   // stale cached flag
        expect(again.classList.contains('msg-failed')).toBe(false);
    });

    it('retryUndelivered resends failed and waiting sends, not in-flight ones', () => {
        let online = false;
        const ss = createSendStatus({ isOnline: () => online });
        const r1 = vi.fn(); const r2 = vi.fn(); const r3 = vi.fn();
        els['msg-b1'] = mkMsgEl(); els['msg-b2'] = mkMsgEl(); els['msg-b3'] = mkMsgEl();
        ss.track('b1', r1);                    // waiting
        online = true;
        ss.track('b2', r2);
        vi.advanceTimersByTime(18000);         // b2 failed
        ss.track('b3', r3);                    // in flight
        expect(ss.retryUndelivered()).toBe(2);
        expect(r1).toHaveBeenCalledTimes(1);
        expect(r2).toHaveBeenCalledTimes(1);
        expect(r3).not.toHaveBeenCalled();
        expect(els['msg-b2'].classList.contains('msg-pending')).toBe(true);
    });

    it('keeps the pod note across a redraw until the write lands', async () => {
        const ss = createSendStatus({ podNoteText: () => 'Not sent' });
        els['msg-p9'] = mkMsgEl();
        await ss.trackPodWrite('p9', async () => false);
        const el = fresh();
        els['msg-p9'] = el;
        ss.applyTo(el, { message_id: 'p9' });
        expect(podNote(el)._kids[0].textContent).toBe('Not sent');
        ss.podSaved('p9');
        const again = fresh();
        els['msg-p9'] = again;
        ss.applyTo(again, { message_id: 'p9' });
        expect(podNote(again)).toBeUndefined();
    });

    it('markPodUnsaved shows a restored queue entry whose Retry replays the queue', () => {
        const onPodRetry = vi.fn();
        const ss = createSendStatus({ onPodRetry });
        const el = fresh();
        els['msg-p8'] = el;
        ss.markPodUnsaved('p8');
        podNote(el)._kids[1]._on();
        expect(onPodRetry).toHaveBeenCalledWith('p8');
    });
});
