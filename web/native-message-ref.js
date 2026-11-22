// Native transfer UUIDs are wire ids; account-scoped history/DOM keys are not.
// References describe authorship relative to the authenticated action actor so
// a gateway's public actor alias need not equal its owner's local browser DID.
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const ACTOR = /^did:key:[A-Za-z0-9_-]{1,2040}$/;
const HISTORY = /^native:(did:key:[A-Za-z0-9_-]{1,2040}):([A-Za-z0-9_-]{1,128})$/;
const actor = value => typeof value === 'string' && ACTOR.test(value);

export function validNativeMessageRef(ref) {
    try {
        return !!ref && typeof ref === 'object' && !Array.isArray(ref)
            && Object.keys(ref).sort().join(',') === 'author,id,kind,v'
            && ref.v === 1 && ref.kind === 'native_file' && typeof ref.id === 'string' && ID.test(ref.id)
            && ['actor', 'peer'].includes(ref.author);
    } catch { return false; }
}

function participants({ threadId, selfWebId, peerWebId } = {}) {
    return typeof threadId === 'string' && threadId.length > 0 && threadId.length <= 2048
        && actor(selfWebId) && actor(peerWebId) && selfWebId !== peerWebId;
}

// Existing records can be recognised without rewriting their private key or
// attachment Blob. Every parsed component must agree with the local record and
// the caller's original conversation; an arbitrary native-looking id is not
// sufficient evidence that a message belongs to this file protocol.
export function nativeMessageRef(message, context) {
    if (!participants(context) || !message || message.thread_id !== context.threadId
        || !message.file || typeof message.file !== 'object' || Array.isArray(message.file)
        || typeof message.message_id !== 'string') return null;
    const match = HISTORY.exec(message.message_id);
    if (!match || message.from_webid !== match[1]) return null;
    const author = match[1] === context.selfWebId ? 'actor' : match[1] === context.peerWebId ? 'peer' : null;
    return author ? { v: 1, kind: 'native_file', id: match[2], author } : null;
}

// The action actor must have been authenticated by the caller. This helper
// performs local resolution only; it cannot authorise a network edit/delete.
export function resolveNativeMessageRef(ref, { actorWebId, messages, ...context } = {}) {
    if (!validNativeMessageRef(ref) || !participants(context) || !Array.isArray(messages)
        || ![context.selfWebId, context.peerWebId].includes(actorWebId)) return null;
    const other = actorWebId === context.selfWebId ? context.peerWebId : context.selfWebId;
    const author = ref.author === 'actor' ? actorWebId : other;
    const matches = messages.filter(message => message?.from_webid === author
        && nativeMessageRef(message, context)?.id === ref.id);
    return matches.length === 1 ? matches[0] : null;
}
