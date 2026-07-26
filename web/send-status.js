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

const CONFIRM_TIMEOUT_MS = 18000;

export function createSendStatus() {
    const pending = new Map();   // msgId -> { timer, resend }

    function _el(msgId) { return document.getElementById('msg-' + msgId); }

    function _clearNote(el) { el?.querySelector('.msg-fail-note')?.remove(); }

    // Register an optimistic send. `resend` re-sends the original bytes.
    function track(msgId, resend) {
        if (!msgId) return;
        const prior = pending.get(msgId);
        if (prior) clearTimeout(prior.timer);
        const timer = setTimeout(() => _fail(msgId), CONFIRM_TIMEOUT_MS);
        pending.set(msgId, { timer, resend });
    }

    // Success — the echo or fanout ack arrived.
    function confirm(msgId) {
        const rec = pending.get(msgId);
        if (rec) { clearTimeout(rec.timer); pending.delete(msgId); }
        const el = _el(msgId);
        if (el) { el.classList.remove('msg-pending', 'msg-failed'); _clearNote(el); }
    }

    // Failure — a correlated error, or (internally) the timeout.
    function fail(msgId) { _fail(msgId); }

    function _fail(msgId) {
        const rec = pending.get(msgId);
        if (rec) clearTimeout(rec.timer);           // keep rec (retain resend), drop the timer
        const el = _el(msgId);
        if (!el || !el.classList.contains('msg-pending')) return;   // already confirmed / gone
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

    function _retry(msgId) {
        const rec = pending.get(msgId);
        const el = _el(msgId);
        if (el) { el.classList.remove('msg-failed'); el.classList.add('msg-pending'); _clearNote(el); }
        if (rec && rec.resend) {
            try { rec.resend(); } catch (_) { /* stays pending; timeout re-fails */ }
            const timer = setTimeout(() => _fail(msgId), CONFIRM_TIMEOUT_MS);
            pending.set(msgId, { timer, resend: rec.resend });
        }
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
        const el = _el(msgId);
        if (!el) return ok;
        el.querySelector('.msg-pod-note')?.remove();   // clear any prior failure note
        if (ok) return true;
        const note = document.createElement('span');
        note.className = 'msg-pod-note';
        const label = document.createElement('span');
        label.textContent = t('send.notSavedToPod');
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'msg-retry-btn';
        retry.textContent = t('send.retry');
        retry.addEventListener('click', () => { note.remove(); trackPodWrite(msgId, writeFn); });
        note.appendChild(label);
        note.appendChild(retry);
        (el.querySelector('.msg-content') || el).appendChild(note);
        return false;
    }

    return { track, confirm, fail, trackPodWrite };
}
