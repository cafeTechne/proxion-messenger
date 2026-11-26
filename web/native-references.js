import { encodeNativeReply, decodeNativeReply, isNativeReplyPlain, resolveNativeReply } from './native-reply-codec.js';
import { encodeNativeReaction, decodeNativeReaction, isNativeReactionPlain } from './native-reaction-codec.js';
import { encodeNativeAction, decodeNativeAction, isNativeActionPlain } from './native-action-codec.js';
import { nativeMessageIdForRef } from './native-message-ref.js';

// Capability state belongs to a single authenticated connection. No prepared
// reference is downgraded to a legacy send when that connection changes.
export function createNativeReferences({ getScope, getGeneration = () => 0, isActive, send, getPeerPub,
    getOwnPub, contextForView, contextForMessage, getMessage, acceptReaction = async () => false, acceptAction = async () => false, historyEnabled = () => true }) {
    let version = 0, actionVersion = 0, epoch = 0;
    const pending = new Map();
    const available = () => version === 1 && isActive() && historyEnabled();
    function stop() {
        epoch++; version = 0; actionVersion = 0;
        for (const row of [...pending.values()]) row.finish(null);
    }
    async function prepare(original, view, input, callerCurrent, encode, action = false) {
        const context = contextForView(view), encoded = encode(original, context, input);
        const scope = getScope(), generation = getGeneration(), stamp = epoch, pub = getPeerPub(context.peerWebId);
        const current = () => available() && (!action || actionVersion === 1) && callerCurrent() && stamp === epoch && scope === getScope()
            && generation === getGeneration() && pub === getPeerPub(context.peerWebId)
            && JSON.stringify(contextForView(view)) === JSON.stringify(context);
        if (!current() || !/^[A-Za-z0-9_-]{43}$/.test(pub || '') || pending.size >= 10) throw Error('Native references unavailable');
        const result = await new Promise(resolve => {
            const id = crypto.randomUUID();
            const finish = value => {
                const row = pending.get(id);
                if (!row) return;
                clearTimeout(row.timer); pending.delete(id); resolve(value);
            };
            const request = { cert_id: view.id, file_id: encoded.body.ref.id, author: encoded.body.ref.author,
                expected_pub: pub, peer_webid: context.peerWebId, ...(action ? { native_action_v: 1 } : {}) };
            const timer = setTimeout(() => finish(null), 6000);
            pending.set(id, { request, current, finish, timer });
            try { if (send({ cmd: 'native_ref_capability', native_ref_v: 1, request_id: id,
                cert_id: request.cert_id, file_id: request.file_id, author: request.author, expected_pub: pub, ...(action ? { native_action_v: 1 } : {}) }) === false) finish(null); }
            catch { finish(null); }
        });
        if (!current() || !result) throw Error('Native reference peer unavailable');
        return { ...encoded, primaryDeviceId: result.primary_device_id, peerPub: pub };
    }
    return { available, actionsAvailable: () => available() && actionVersion === 1, stop,
        start(value, actionValue = 0) { stop(); version = value === 1 ? 1 : 0; actionVersion = actionValue === 1 ? 1 : 0; },
        prepareReply(original, view, input, current = () => true) { return prepare(original, view, input, current, encodeNativeReply); },
        prepareReaction(original, view, input, current = () => true) { return prepare(original, view, input, current, encodeNativeReaction); },
        prepareAction(original, view, input, current = () => true) { return prepare(original, view, input, current, encodeNativeAction, true); },
        handleEvent(event) {
            if (event?.type !== 'native_ref_capability_result') return false;
            const row = pending.get(event.request_id);
            if (row?.current() && event.native_ref_v === 1 && typeof event.supported === 'boolean'
                && (!row.request.native_action_v || event.native_action_v === 1)
                && ['file_id', 'author', 'expected_pub', 'peer_webid'].every(key => event[key] === row.request[key])) {
                row.finish(event.supported && event.peer_pub === row.request.expected_pub
                    && typeof event.primary_device_id === 'string' && /^did:key:[A-Za-z0-9_-]{1,2040}$/.test(event.primary_device_id) ? event : null);
            }
            return true;
        },
        async transform(message, current) {
            if (message?._native_ref_v !== 1) return isNativeReplyPlain(message?.content) || isNativeReactionPlain(message?.content) || isNativeActionPlain(message?.content) ? null : message;
            if (!available() || !current() || message._nativeRefPub !== getOwnPub()) return null;
            const action = decodeNativeAction(message.content, message.message_id);
            if (message._nativeActionV !== undefined && (message._nativeActionV !== 1 || actionVersion !== 1 || !action)
                || action && message._nativeActionV !== 1) return null;
            const reaction = decodeNativeReaction(message.content, message.message_id);
            const body = action || reaction || decodeNativeReply(message.content, message.message_id);
            if (!body) return null;
            const context = contextForMessage(message), scope = getScope(), generation = getGeneration(), stamp = epoch, pub = getOwnPub();
            const validCurrent = () => available() && current() && scope === getScope() && generation === getGeneration() && stamp === epoch
                && pub === getOwnPub() && JSON.stringify(contextForMessage(message)) === JSON.stringify(context);
            const id = nativeMessageIdForRef(body.ref, context);
            if (!id) return null;
            if (action || reaction) {
                // A control has no chat row. Its durable state must commit before
                // the protected reader is allowed to acknowledge consumption.
                if (!validCurrent() || await (action ? acceptAction(action, context, validCurrent) : acceptReaction(reaction, context, validCurrent)) !== true || !validCurrent()) return null;
                return message;
            }
            const original = await getMessage(id);
            if (!validCurrent()) return null;
            const resolved = resolveNativeReply(body, { ...context, messages: original ? [original] : [] });
            if (!resolved) return null;
            return { ...message, ...resolved };
        },
    };
}
