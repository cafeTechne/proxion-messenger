// One guarded navigation transaction across Saved, search, replies and pins.
// Callbacks resolve actual retained messages through their protocol adapters;
// snapshots are references, never replacement messages. Opening and focusing
// are synchronous UI operations; asynchronous resolvers/revealers must honour
// their current() guard before changing host state.
export function createReferenceNavigation({ getScope, getView, getViewRevision, findConversation, openConversation,
    resolveMessage, revealMessage, focusMessage, onUnavailable = () => {} }) {
    let epoch = 0;
    function cancel() { epoch++; }
    async function navigate(reference) {
        const stamp = ++epoch, scope = getScope(), origin = getViewRevision();
        const sameScope = () => stamp === epoch && scope === getScope();
        const validRef = reference && typeof reference.thread_id === 'string' && reference.thread_id.length > 0 && reference.thread_id.length <= 4096
            && typeof reference.message_id === 'string' && reference.message_id.length > 0 && reference.message_id.length <= 4096;
        if (!validRef) { if (sameScope()) onUnavailable('unavailable'); return false; }
        const ref = { ...reference };
        let target;
        try { target = findConversation(ref); } catch { /* unavailable */ }
        if (!sameScope() || origin !== getViewRevision()) return false;
        if (!target || target.id !== ref.thread_id || ref.thread_type && target.type !== ref.thread_type) {
            onUnavailable('conversation'); return false;
        }
        let stillCurrent = () => sameScope() && origin === getViewRevision();
        try {
            const before = getView();
            if (before?.id !== target.id || before?.type !== target.type) {
                if (openConversation(target, ref) === false) { if (sameScope()) onUnavailable('conversation'); return false; }
            }
            const selected = { ...getView() }, revision = getViewRevision();
            const identity = view => JSON.stringify([view?.id, view?.type, view?.peerWebid || view?.peerDid || '']);
            const current = () => {
                if (!sameScope() || revision !== getViewRevision() || identity(getView()) !== identity(selected)) return false;
                try { const listed = findConversation(ref); return listed?.id === target.id && listed?.type === target.type; }
                catch { return false; }
            };
            stillCurrent = current;
            if (!current()) return false;
            if (selected?.id !== target.id || selected?.type !== target.type) { onUnavailable('conversation'); return false; }
            const message = await resolveMessage(ref, selected, current);
            if (!current()) return false;
            if (!message || message.message_id !== ref.message_id || message.thread_id !== ref.thread_id || message.deleted) {
                onUnavailable(message?.deleted ? 'deleted' : 'unavailable'); return false;
            }
            const element = await revealMessage(message, selected, current);
            if (!current()) return false;
            if (!element || element.dataset?.messageId !== ref.message_id) { onUnavailable('unavailable'); return false; }
            return focusMessage(element) !== false;
        } catch {
            if (stillCurrent()) onUnavailable('unavailable');
            return false;
        }
    }
    return { navigate, cancel };
}
