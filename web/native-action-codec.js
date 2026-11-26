import { encodeSealedPlain, decodeSealedPlain } from './e2e-secondary.js';
import { nativeMessageRef, validNativeMessageRef } from './native-message-ref.js';
const KIND = 'native_action';
const bytes = new TextEncoder();
export function validNativeAction(body) {
    return !!body && typeof body === 'object' && !Array.isArray(body)
        && typeof body.mid === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(body.mid)
        && validNativeMessageRef(body.ref) && ['delete', 'pin', 'unpin'].includes(body.op)
        && (body.op !== 'delete' || body.ref.author === 'actor')
        && Number.isSafeInteger(body.clock) && body.clock > 0 && body.clock <= 2147483647;
}
export function isNativeActionPlain(plaintext) {
    if (typeof plaintext !== 'string' || !plaintext.startsWith('\u0000pxs1')) return false;
    try { return JSON.parse(plaintext.slice(5))?.k === KIND; } catch { return false; }
}
export function encodeNativeAction(original, context, { messageId, operation, clock } = {}) {
    const body = { mid: messageId, ref: nativeMessageRef(original, context), op: operation, clock };
    if (!validNativeAction(body)) throw Error('Invalid native action');
    return { body, plaintext: encodeSealedPlain(KIND, body) };
}
// Only unpin can use an already retained pin reference without an original.
// The caller must validate local state/context and obtain the normal signed
// parent preflight. This never creates a placeholder message or file.
export function encodeRetainedNativeUnpin(ref, { messageId, clock } = {}) {
    const body = { mid: messageId, ref: { ...ref }, op: 'unpin', clock };
    if (!validNativeAction(body)) throw Error('Invalid retained native pin');
    return { body, plaintext: encodeSealedPlain(KIND, body) };
}
export function decodeNativeAction(plaintext, messageId) {
    if (typeof plaintext !== 'string' || bytes.encode(plaintext).length > 4096) return null;
    const body = decodeSealedPlain(plaintext, KIND);
    return validNativeAction(body) && Object.keys(body).sort().join(',') === 'clock,k,mid,op,ref,v' && body.mid === messageId ? body : null;
}
