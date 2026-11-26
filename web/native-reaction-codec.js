import { encodeSealedPlain, decodeSealedPlain } from './e2e-secondary.js';
import { nativeMessageRef, validNativeMessageRef } from './native-message-ref.js';

const KIND = 'native_reaction';
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const TOKEN = /^~[A-Za-z0-9_-]{11}$/;
const bytes = new TextEncoder();
export function validNativeReaction(body) {
    return !!body && typeof body === 'object' && !Array.isArray(body)
        && typeof body.mid === 'string' && ID.test(body.mid) && validNativeMessageRef(body.ref)
        && typeof body.tok === 'string' && TOKEN.test(body.tok)
        && typeof body.emoji === 'string' && body.emoji.length > 0 && body.emoji.length <= 64
        && !body.emoji.includes('\u0000') && body.emoji === body.emoji.trim()
        && Number.isSafeInteger(body.seq) && body.seq > 0 && body.seq <= 2147483647
        && ['add', 'remove'].includes(body.op);
}
export function isNativeReactionPlain(plaintext) {
    if (typeof plaintext !== 'string' || !plaintext.startsWith('\u0000pxs1')) return false;
    try { return JSON.parse(plaintext.slice(5))?.k === KIND; } catch { return false; }
}
export function encodeNativeReaction(original, context, { messageId, token, emoji, sequence, add } = {}) {
    const ref = nativeMessageRef(original, context);
    const body = { mid: messageId, ref, tok: token, emoji, seq: sequence, op: add === true ? 'add' : add === false ? 'remove' : null };
    if (!validNativeReaction(body)) throw Error('Invalid native reaction');
    return { body, plaintext: encodeSealedPlain(KIND, body) };
}
export function decodeNativeReaction(plaintext, messageId) {
    if (typeof plaintext !== 'string' || bytes.encode(plaintext).length > 4096) return null;
    const body = decodeSealedPlain(plaintext, KIND);
    return validNativeReaction(body) && Object.keys(body).sort().join(',') === 'emoji,k,mid,op,ref,seq,tok,v'
        && body.mid === messageId ? body : null;
}
