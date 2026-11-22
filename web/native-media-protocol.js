export const NATIVE_CHUNK_BYTES = 65536;
export const NATIVE_FILE_BYTES = 25 * 1024 * 1024;
export const NATIVE_HEADER_KEYS = ['e2e', 'e2e_v', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub'];
const fields = [...NATIVE_HEADER_KEYS, 'size', 'cipher_digest', 'chunk_count', 'recipient_pub'];

export function nativeChunkManifest(event) {
    const manifest = Object.fromEntries(fields.map(key => [key, event?.[key]]));
    if (manifest.e2e !== true || manifest.e2e_v !== 1 || typeof manifest.content !== 'string'
        || !/^[A-Za-z0-9_-]{1,4096}$/.test(manifest.content) || !/^[A-Za-z0-9_-]{1,32}$/.test(manifest.nonce || '')
        || !/^[A-Za-z0-9_-]{1,64}$/.test(manifest.ratchet_pub || '')
        || !Number.isSafeInteger(manifest.msg_num) || manifest.msg_num < 0 || manifest.msg_num >= 2 ** 31
        || !Number.isSafeInteger(manifest.pn) || manifest.pn < 0 || manifest.pn >= 2 ** 31
        || !Number.isSafeInteger(manifest.size) || manifest.size < 17 || manifest.size > NATIVE_FILE_BYTES
        || manifest.chunk_count !== Math.ceil(manifest.size / NATIVE_CHUNK_BYTES)
        || !/^[a-f0-9]{64}$/.test(manifest.cipher_digest || '') || !/^[A-Za-z0-9_-]{43}$/.test(manifest.recipient_pub || '')) {
        throw Error('Invalid native chunk manifest');
    }
    return manifest;
}

