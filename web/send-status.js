// send-status.js — R66: optimistic-message failure handling.
//
// A sent message renders instantly as .msg-pending; the server echo (or a
// fanout ack) clears it. Without this, a message that NEVER confirms (the
// gateway rejects it, it's dropped from the overflow queue during a long
// outage, or it's silently lost) stays faded forever with no signal — silent
// message loss, the worst failure mode for a messenger.
//
// This tracks each optimistic send. If it isn't confirmed within a timeout, the
// message is marked "Not delivered" with a Retry affordance. Retry re-sends the
// exact bytes that were originally sent (same message_id + ciphertext), which
// is a safe gap-fill for a never-delivered message.

import { t } from './i18n.js';
import { announce } from './a11y.js';

const CONFIRM_TIMEOUT_MS = 18000;

// "Seen by A, B, C and 2 more" for a read-receipt row, other readers only.
// Plain text for a textContent sink: names are not HTML-escaped here (that
// showed "A&amp;B" for a reader named "A&B"). Returns "" with no readers.
export function seenByText(readers, selfWebId) {
    const others = (readers || []).filter(r => r.receiver_webid !== selfWebId);
    if (!others.length) return '';
    const names = others.slice(0, 3).map(r => r.display_name || String(r.receiver_webid || '').slice(-8)).join(', ');
    return others.length <= 3
        ? t('msg.seenBy', { names })
        : t('msg.seenByMore', { names, count: others.length - 3 });
}

// isOnline (optional): true when the socket is OPEN. A message sent while
// offline sits in the connection queue and goes out on reconnect, so its
// confirm timer must not run until then; it shows "Waiting for connection"
// instead, and resumeWaiting() (called after the queue flushes) arms it.
// onRetry (optional): runs before a Retry resends, so the connection can be
// checked first (a half-open socket would swallow the resend).
// onState (optional): (msgId, 'pending' | 'failed' | null) whenever a tracked
// send changes state, so the caller can keep it in the message record.
// resendFor (optional): msgId -> resend function, for a send restored from a
// record (after a reload) that has no in-memory resend.
// podNoteText / onPodRetry (optional): the wording of the pod-write note, and
// what its Retry does for a write restored from the durable queue.
//
// The state lives here, not only on the bubble: switching threads or reloading
// redraws the feed, and applyTo() paints each redrawn bubble from it.
export function createSendStatus({ isOnline, onRetry, onState, resendFor, podNoteText, onPodRetry } = {}) {
    const pending = new Map();      // msgId -> { timer, resend, waiting, failed }
    const podUnsaved = new Map();   // msgId -> pod write to retry (null: replay the queue)
    const settled = new Set();      // confirmed ids, so a stale stored flag is not repainted

    function _el(msgId) { return document.getElementById('msg-' + msgId); }

    function _clearNote(el) { el?.querySelector('.msg-fail-note')?.remove(); }

    function _clearWaitNote(el) { el?.querySelector('.msg-wait-note')?.remove(); }

    function _state(msgId, st) {
        if (onState) { try { onState(msgId, st); } catch (_) { /* best-effort */ } }
    }

    function _paintWaiting(el) {
        if (!el) return;
        el.classList.add('msg-pending');
        if (el.querySelector('.msg-wait-note')) return;
        const note = document.createElement('span');
        note.className = 'msg-wait-note';
        note.textContent = t('send.waitingForConnection');
        (el.querySelector('.msg-content') || el).appendChild(note);
    }

    function _arm(msgId, resend) {
        if (isOnline && !isOnline()) {
            pending.set(msgId, { timer: null, resend, waiting: true, failed: false });
            _paintWaiting(_el(msgId));
            return;
        }
        const timer = setTimeout(() => _fail(msgId), CONFIRM_TIMEOUT_MS);
        pending.set(msgId, { timer, resend, waiting: false, failed: false });
    }

    // Register an optimistic send. `resend` re-sends the original bytes.
    function track(msgId, resend) {
        if (!msgId) return;
        const prior = pending.get(msgId);
        if (prior) clearTimeout(prior.timer);
        settled.delete(msgId);
        _arm(msgId, resend);
        _state(msgId, 'pending');
    }

    // The offline queue was just flushed: start the confirm timer for every
    // message that was waiting for the connection.
    function resumeWaiting() {
        for (const [msgId, rec] of pending) {
            if (!rec.waiting) continue;
            _clearWaitNote(_el(msgId));
            rec.waiting = false;
            rec.timer = setTimeout(() => _fail(msgId), CONFIRM_TIMEOUT_MS);
        }
    }

    // Success — the echo or fanout ack arrived.
    function confirm(msgId) {
        const rec = pending.get(msgId);
        if (rec) { clearTimeout(rec.timer); pending.delete(msgId); settled.add(msgId); _state(msgId, null); }
        const el = _el(msgId);
        if (el) { el.classList.remove('msg-pending', 'msg-failed'); _clearNote(el); _clearWaitNote(el); }
    }

    // Failure — a correlated error, or (internally) the timeout.
    function fail(msgId) { _fail(msgId); }

    function _paintFailed(el, msgId) {
        if (!el) return;
        el.classList.remove('msg-pending');
        el.classList.add('msg-failed');
        if (el.querySelector('.msg-fail-note')) return;
        const note = document.createElement('span');
        note.className = 'msg-fail-note';
        const label = document.createElement('span');
        label.textContent = t('send.notDelivered');
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'msg-retry-btn';
        retry.textContent = t('send.retry');
        retry.addEventListener('click', () => _retry(msgId));
        note.appendChild(label);
        note.appendChild(retry);
        (el.querySelector('.msg-content') || el).appendChild(note);
    }

    function _fail(msgId) {
        let rec = pending.get(msgId);
        const el = _el(msgId);
        _clearWaitNote(el);
        if (rec) { clearTimeout(rec.timer); rec.timer = null; rec.waiting = false; }   // keep rec (retain resend)
        // A drawn bubble that is no longer pending was confirmed by another path.
        if (el && !el.classList.contains('msg-pending')) {
            if (rec && !rec.failed) pending.delete(msgId);
            return;
        }
        // Not drawn (another thread is open): the failure is kept on the record
        // so the bubble shows it when the thread is drawn again.
        if (!el && !rec) return;
        if (!rec) { rec = { timer: null, resend: null, waiting: false, failed: true }; pending.set(msgId, rec); }
        rec.failed = true;
        _state(msgId, 'failed');
        if (!el) return;
        const shown = !!el.querySelector('.msg-fail-note');
        _paintFailed(el, msgId);
        // A delivery failure is important enough to interrupt: mirror it to the
        // assertive live region (the note is appended into an already-rendered
        // message, which screen readers announce inconsistently on their own).
        if (!shown) announce(t('send.notDelivered'), true);
    }

    // The gateway refused a send for now (rate limit): resend it after
    // `delayMs` and keep the bubble pending instead of failing it. After a
    // few refusals in a row it fails, with the usual Retry.
    function retryLater(msgId, delayMs) {
        const rec = pending.get(msgId);
        if (!rec || !rec.resend || (rec.rateRetries || 0) >= 3) { _fail(msgId); return; }
        rec.rateRetries = (rec.rateRetries || 0) + 1;
        clearTimeout(rec.timer);
        rec.waiting = false;
        rec.timer = setTimeout(() => {
            try { rec.resend(); } catch (_) { /* the confirm timer below re-fails it */ }
            _arm(msgId, rec.resend);
            const again = pending.get(msgId);
            if (again) again.rateRetries = rec.rateRetries;
        }, Math.max(0, delayMs));
    }

    function _resendOf(msgId) {
        const rec = pending.get(msgId);
        if (rec && rec.resend) return rec.resend;
        if (!resendFor) return null;
        try { return resendFor(msgId) || null; } catch (_) { return null; }
    }

    function _retry(msgId) {
        if (onRetry) { try { onRetry(); } catch (_) { /* best-effort */ } }
        _resend(msgId);
    }

    function _resend(msgId) {
        const resend = _resendOf(msgId);
        const el = _el(msgId);
        if (el) { el.classList.remove('msg-failed'); el.classList.add('msg-pending'); _clearNote(el); _clearWaitNote(el); }
        if (resend) {
            try { resend(); } catch (_) { /* stays pending; timeout re-fails */ }
        }
        _arm(msgId, resend);   // with nothing to resend it simply fails again on the timer
    }

    // Connectivity is back: resend every send that failed or is still waiting.
    // Returns how many were resent.
    function retryUndelivered() {
        let n = 0;
        for (const [msgId, rec] of [...pending]) {
            if (!rec.failed && !rec.waiting) continue;
            _resend(msgId);
            n++;
        }
        return n;
    }

    // Is this send still unconfirmed (pending, waiting or failed)?
    function isUndelivered(msgId) { return pending.has(msgId); }

    function _paintPodNote(el, msgId) {
        if (!el) return;
        el.querySelector('.msg-pod-note')?.remove();
        const note = document.createElement('span');
        note.className = 'msg-pod-note';
        const label = document.createElement('span');
        label.textContent = podNoteText ? podNoteText() : t('send.notSavedToPod');
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'msg-retry-btn';
        retry.textContent = t('send.retry');
        retry.addEventListener('click', () => {
            note.remove();
            const writeFn = podUnsaved.get(msgId);
            if (writeFn) trackPodWrite(msgId, writeFn);
            else if (onPodRetry) { try { onPodRetry(msgId); } catch (_) { /* best-effort */ } }
        });
        note.appendChild(label);
        note.appendChild(retry);
        (el.querySelector('.msg-content') || el).appendChild(note);
    }

    // D2 (write-through): run a pod write and, if it fails, show a "not saved to
    // your pod" note with a Retry that re-runs it. This is DISTINCT from delivery
    // (.msg-pending / "Not delivered", which is the gateway echo): a message can be
    // delivered to room members in real time yet not yet be durable in the pod. It
    // stops the old silent swallow of a failed pod write. Returns the write result.
    async function trackPodWrite(msgId, writeFn) {
        if (!msgId || typeof writeFn !== 'function') return false;
        let ok = false;
        try { ok = await writeFn(); } catch (_) { ok = false; }
        if (ok) podUnsaved.delete(msgId); else podUnsaved.set(msgId, writeFn);
        const el = _el(msgId);
        if (!el) return ok;
        el.querySelector('.msg-pod-note')?.remove();   // clear any prior failure note
        if (ok) return true;
        _paintPodNote(el, msgId);
        return false;
    }

    // A pod write restored from the durable queue (after a reload): show its note.
    function markPodUnsaved(msgId) {
        if (!msgId || podUnsaved.has(msgId)) return;
        podUnsaved.set(msgId, null);
        _paintPodNote(_el(msgId), msgId);
    }

    // A queued pod write finally landed.
    function podSaved(msgId) {
        podUnsaved.delete(msgId);
        _el(msgId)?.querySelector('.msg-pod-note')?.remove();
    }

    // Paint a freshly drawn bubble from the tracked state (thread switch, reload).
    // `msg.delivery` is the flag a stored record carries for a send that was not
    // confirmed before the page went away.
    function applyTo(el, msg) {
        const msgId = msg && msg.message_id;
        if (!el || !msgId) return;
        let rec = pending.get(msgId);
        if (!rec && msg.delivery && !settled.has(msgId)) {
            rec = { timer: null, resend: null, waiting: false, failed: true };
            pending.set(msgId, rec);
        }
        if (rec) {
            if (rec.failed) _paintFailed(el, msgId);
            else if (rec.waiting) _paintWaiting(el);
            else el.classList.add('msg-pending');
        }
        if (podUnsaved.has(msgId)) _paintPodNote(el, msgId);
    }

    return {
        track, confirm, fail, retryLater, trackPodWrite, resumeWaiting,
        applyTo, retryUndelivered, isUndelivered, markPodUnsaved, podSaved,
    };
}
