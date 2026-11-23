import { encodeSealedPlain, decodeSealedPlain } from './e2e-secondary.js';
import { nativeMessageRef, validNativeMessageRef, resolveNativeMessageRef } from './native-message-ref.js';

export const NATIVE_REPLY_KIND = 'native_reply';
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const DID = /^did:key:[A-Za-z0-9_-]{1,2040}$/;
const bytes = new TextEncoder();
function valid(body) {
    return body && typeof body === 'object' && !Array.isArray(body)
        && typeof body.mid === 'string' && ID.test(body.mid) && validNativeMessageRef(body.ref)
        && typeof body.text === 'string' && body.text.length > 0 && body.text === body.text.trim()
        && !body.text.includes('\u0000') && bytes.encode(body.text).length <= 8192;
}

// Replies travel as normal encrypted messages, containing a validated native
// reference rather than a private UI key. This codec does not encrypt or send.
export function encodeNativeReply(original, context, { messageId, text } = {}) {
    const ref = nativeMessageRef(original, context);
    if (!ref || typeof text !== 'string') throw Error('Invalid native reply reference');
    const body = { mid: messageId, ref, text: text.trim().normalize('NFC') };
    if (!valid(body)) throw Error('Invalid native reply payload');
    const plaintext = encodeSealedPlain(NATIVE_REPLY_KIND, body);
    if (bytes.encode(plaintext).length > 12000) throw Error('Native reply too large');
    return { body, plaintext };
}

export function decodeNativeReply(plaintext, messageId) {
    if (typeof plaintext !== 'string' || bytes.encode(plaintext).length > 12000) return null;
    const body = decodeSealedPlain(plaintext, NATIVE_REPLY_KIND);
    return valid(body) && Object.keys(body).sort().join(',') === 'k,mid,ref,text,v' && body.mid === messageId ? body : null;
}

// Call only after authenticating the message actor. A missing/ambiguous local
// original leaves a bounded reference on the reply; it cannot create a file or
// choose the other author's same-UUID attachment.
export function resolveNativeReply(body, context) {
    if (!valid(body) || !context || typeof context.threadId !== 'string' || !context.threadId || context.threadId.length > 2048
        || typeof context.selfWebId !== 'string' || !DID.test(context.selfWebId)
        || typeof context.peerWebId !== 'string' || !DID.test(context.peerWebId) || context.selfWebId === context.peerWebId
        || ![context.selfWebId, context.peerWebId].includes(context.actorWebId) || !Array.isArray(context.messages)) return null;
    const original = resolveNativeMessageRef(body.ref, context);
    return { content: body.text, reply_to_id: original?.message_id || null,
        native_reply_ref: { ...body.ref }, native_reply_actor: context?.actorWebId || '',
        native_reply_unavailable: !original };
}
