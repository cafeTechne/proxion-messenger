import { encodeSealedPlain, decodeSealedPlain } from './e2e-secondary.js';
import { nativeMessageRef, validNativeMessageRef } from './native-message-ref.js';

export const NATIVE_EDIT_KIND = 'native_edit';
export const MAX_NATIVE_EDIT_SEQUENCE = 100;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const SHA = /^[a-f0-9]{64}$/;
const bytes = new TextEncoder();
const fail = code => { const error = Error(code); error.name = 'NativeEditValidationError'; error.code = code; throw error; };

function validBody(body) {
    return body && typeof body === 'object' && !Array.isArray(body)
        && typeof body.action_id === 'string' && ID.test(body.action_id)
        && validNativeMessageRef(body.ref) && body.ref.author === 'actor' && body.action_id !== body.ref.id
        && Number.isSafeInteger(body.seq) && body.seq >= 1 && body.seq <= MAX_NATIVE_EDIT_SEQUENCE
        && typeof body.parent === 'string' && SHA.test(body.parent)
        && typeof body.text === 'string' && body.text === body.text.trim() && body.text.length > 0
        && !body.text.includes('\u0000') && bytes.encode(body.text).length <= 8192;
}

// Validate ownership/context and complete byte bounds before asking the shared
// ratchet to prepare anything. This is only a codec: transport must separately
// prove capability, freeze the peer key and journal the original send id.
export function encodeNativeEdit(message, context, { actionId, text, sequence, parentHeaderDigest } = {}) {
    const ref = nativeMessageRef(message, context);
    if (!ref || ref.author !== 'actor') fail('native-edit-reference');
    const previous = message.native_edit_seq ?? 0;
    if (!Number.isSafeInteger(previous) || previous < 0 || sequence !== previous + 1) fail('native-edit-sequence');
    if (typeof text !== 'string') fail('native-edit-text');
    const body = { action_id: actionId, ref, seq: sequence, parent: parentHeaderDigest, text: text.trim().normalize('NFC') };
    if (!validBody(body)) fail('native-edit-payload');
    const plaintext = encodeSealedPlain(NATIVE_EDIT_KIND, body);
    // GCM tag plus base64url must stay inside the existing 16 KiB sealed edit
    // field. Escaping in JSON also counts; a character count is insufficient.
    if (bytes.encode(plaintext).length > 12000) fail('native-edit-size');
    return { plaintext, body };
}

// Bind the encrypted action to the gateway's authenticated opaque event. UI
// message keys never appear as wire target ids. Unknown versions/extra fields
// and altered routing annotations cannot become an edit of a different file.
export function decodeNativeEdit(plaintext, event) {
    if (typeof plaintext !== 'string' || bytes.encode(plaintext).length > 12000) return null;
    const body = decodeSealedPlain(plaintext, NATIVE_EDIT_KIND);
    if (!validBody(body) || Object.keys(body).sort().join(',') !== 'action_id,k,parent,ref,seq,text,v') return null;
    if (event?.type !== 'native_edit' || event.native_edit_v !== 1 || event.message_id !== body.action_id
        || event.file_id !== body.ref.id || event.native_sequence !== body.seq || event.parent_header_digest !== body.parent) return null;
    return body;
}
