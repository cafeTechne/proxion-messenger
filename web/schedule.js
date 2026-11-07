// A scheduled message stays in the composer until the gateway acknowledges it.
// A late reply must never clear text typed since submitting or in another chat.
export function createScheduledComposer({
    send, getDraft, getThreadId, clearDraft, onPending = () => {},
    onFailure = () => {}, onConfirmed = () => {},
    timeoutMs = 15000, setTimer = setTimeout, clearTimer = clearTimeout,
}) {
    let pending = null;

    function finish() {
        const current = pending;
        if (!current) return null;
        pending = null;
        if (current.timer != null) clearTimer(current.timer);
        onPending(false);
        return current;
    }

    function submit(command) {
        if (pending) return false;
        const current = { ...command, draft: getDraft(), timer: null };
        pending = current;
        onPending(true);
        current.timer = setTimer(() => {
            if (pending === current) { finish(); onFailure('unconfirmed'); }
        }, timeoutMs);
        try {
            if (send(command) === false) {
                if (pending === current) { finish(); onFailure('offline'); }
                return false;
            }
        } catch {
            if (pending === current) { finish(); onFailure('offline'); }
            return false;
        }
        return true;
    }

    function handleEvent(event) {
        if (!pending || !event) return false;
        if (event.type === 'error') {
            if (event.cmd !== 'schedule_message' && event.code !== 'E_SCHEDULE_E2E') return false;
            if (event.thread_id && event.thread_id !== pending.thread_id) return false;
            finish();
            // The caller renders the gateway's error; the draft remains intact.
            return true;
        }
        if (!['message_scheduled', 'scheduled_created'].includes(event.type)) return false;
        if (event.thread_id !== pending.thread_id || event.send_at !== pending.send_at) return false;
        const current = finish();
        if (getThreadId() === current.thread_id && getDraft() === current.draft) clearDraft();
        onConfirmed(current);
        return true;
    }

    return { submit, handleEvent, isPending: () => !!pending };
}
