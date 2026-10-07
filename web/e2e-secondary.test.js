/**
 * E2E sealing of DM edits, reactions and files (e2e-secondary.js), run against
 * the real ratchet in e2e.js between two identities. Asserts that nothing the
 * client sends for these events contains the plaintext, that the peer recovers
 * it, and that older plaintext events still read.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';

const _ls = {};
globalThis.localStorage = {
    getItem: k => Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null,
    setItem: (k, v) => { _ls[k] = String(v); },
    removeItem: k => { delete _ls[k]; },
    clear: () => { for (const k in _ls) delete _ls[k]; },
};
vi.mock('./auth.js', () => ({ solidSession: { info: { isLoggedIn: false } }, podStorageRoot: () => null }));
vi.mock('./pod.js', () => ({ podPublishIdentityAcl: async () => true }));

import { initE2E, myX25519PubB64u, cachePeerPub, isE2EEnabled, ratchetEncrypt, ratchetDecrypt, _resetForTesting } from './e2e.js';
import {
    createSecondaryCrypto, createReactionTokens, createSealedOutbox, sealedState, isSealedPlain,
    encryptFile, decryptFile, isReactionToken, SEALED_FILE_NAME, SEALED_FILE_MIME, FILE_TAG_BYTES,
    UNDECRYPTABLE, encodeSealedPlain,
} from './e2e-secondary.js';
import { createFileTransfer } from './filetransfer.js';
import { createEdit } from './edit.js';
import { createReactions } from './reactions.js';
import { applyEditToRecord } from './dmhistory.js';

// ── two parties on one module-level identity ────────────────────────────────
let alice, bob;
async function makePair() {
    const kp = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    return { privJwk: await crypto.subtle.exportKey('jwk', kp.privateKey), pubB64u: (await crypto.subtle.exportKey('jwk', kp.publicKey)).x };
}
async function become(p) {
    _resetForTesting();
    localStorage.setItem('proxion_e2e_x25519_priv_jwk', JSON.stringify(p.privJwk));
    localStorage.setItem('proxion_e2e_x25519_pub_b64u', p.pubB64u);
    await initE2E();
}
function memStore() {
    const m = {};
    return { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); } };
}
function side(selfId) {
    return createSecondaryCrypto({
        encrypt: ratchetEncrypt, decrypt: ratchetDecrypt, isKeyed: isE2EEnabled,
        isSelf: (id) => id === selfId,
        tokens: createReactionTokens({ storage: memStore(), key: 'k' }),
    });
}
// A DM view as Alice sees it (peer id 'bob') and as Bob sees it.
const viewAtAlice = { type: 'dm', id: 'cert-1', peerWebid: 'bob' };

beforeAll(async () => {
    localStorage.clear();
    _resetForTesting();
    await initE2E();
    alice = { pubB64u: myX25519PubB64u(), privJwk: JSON.parse(localStorage.getItem('proxion_e2e_x25519_priv_jwk')) };
    bob = await makePair();
});
beforeEach(async () => {
    // Fresh session per test: new peer ids keep ratchet state independent.
    await become(alice);
});

const _enc = new TextEncoder();
const hasPlain = (obj, s) => JSON.stringify(obj).includes(s);

describe('edits', () => {
    it('a sealed edit carries no plaintext and the peer recovers it', async () => {
        cachePeerPub('bob', bob.pubB64u);
        const a = side('alice');
        const hdr = await a.sealEdit(viewAtAlice, 'm1', 'the secret new wording');
        const cmd = { cmd: 'edit_message', cert_id: 'cert-1', message_id: 'm1', content: 'the secret new wording', ...hdr };
        expect(hasPlain(cmd, 'secret')).toBe(false);
        expect(cmd.e2e).toBe(true);
        expect(cmd.e2e_v).toBe(1);
        // The gateway turns content into new_content on the event.
        const ev = { type: 'message_edited', message_id: 'm1', new_content: cmd.content, from_webid: 'alice',
            e2e: cmd.e2e, e2e_v: cmd.e2e_v, nonce: cmd.nonce, msg_num: cmd.msg_num, pn: cmd.pn, ratchet_pub: cmd.ratchet_pub };
        // Alice's own echo comes back from her outbox, not by decrypting.
        const echo = await a.openEdit({ ...ev });
        expect(echo.new_content).toBe('the secret new wording');

        await become(bob);
        const b = side('bob');
        const got = await b.openEdit(ev);
        expect(got.new_content).toBe('the secret new wording');
        expect(got.e2e).toBe(false);
    });

    it('an edit is bound to the message it was sealed for', async () => {
        cachePeerPub('bob', bob.pubB64u);
        const hdr = await side('alice').sealEdit(viewAtAlice, 'm1', 'x');
        await become(bob);
        const ev = { type: 'message_edited', message_id: 'OTHER', new_content: hdr.content, from_webid: 'alice2', ...hdr };
        delete ev.content;
        expect(await side('bob').openEdit(ev)).toBe(null);
    });

    it('a plaintext edit from an older client still applies', async () => {
        const ev = { type: 'message_edited', message_id: 'm1', new_content: 'old style' };
        expect(await side('bob').openEdit(ev)).toEqual(ev);
    });

    it('an edit of an unknown sealed version is dropped, never shown as text', async () => {
        const ev = { type: 'message_edited', message_id: 'm1', new_content: 'Zm9v', e2e: true, e2e_v: 2, nonce: 'a', ratchet_pub: 'b', msg_num: 0 };
        expect(await side('bob').openEdit(ev)).toBe(null);
    });

    it('a view with no peer key needs no sealing (sent as before)', async () => {
        expect(await side('alice').sealEdit({ type: 'dm', id: 'c', peerWebid: 'nokey' }, 'm', 't')).toBe(null);
        expect(await side('alice').sealEdit({ type: 'local_room', id: 'r' }, 'm', 't')).toBe(null);
    });
});

describe('reactions', () => {
    it('the emoji travels sealed behind a token; the peer and history resolve it', async () => {
        cachePeerPub('bob', bob.pubB64u);
        const a = side('alice');
        const sealed = await a.sealReaction(viewAtAlice, 'm1', '🦄');
        const cmd = { cmd: 'add_reaction', cert_id: 'cert-1', message_id: 'm1', emoji: '🦄', ...sealed };
        expect(hasPlain(cmd, '🦄')).toBe(false);
        expect(isReactionToken(cmd.emoji)).toBe(true);
        expect(cmd.emoji.length).toBeLessThanOrEqual(12);   // gateway emoji field limit
        // Own echo resolves from the token store; removal names the same token.
        const echo = await a.openReaction({ type: 'reaction_added', message_id: 'm1', from_webid: 'alice', ...sealed }, 'add');
        expect(echo.emoji).toBe('🦄');
        expect(a.reactionWireEmoji('m1', '🦄')).toBe(sealed.emoji);

        await become(bob);
        const b = side('bob');
        const ev = { type: 'reaction_added', message_id: 'm1', from_webid: 'alice', ...sealed };
        const got = await b.openReaction(ev, 'add');
        expect(got.emoji).toBe('🦄');
        expect(got.content).toBeUndefined();
        // Stored reactions (local_history) carry only the token.
        expect(b.translateReaction(sealed.emoji)).toBe('🦄');
        const rm = await b.openReaction({ type: 'reaction_removed', message_id: 'm1', from_webid: 'alice', emoji: sealed.emoji }, 'remove');
        expect(rm.emoji).toBe('🦄');
        expect(b.translateReaction(sealed.emoji)).toBe(null);
    });

    it('a token whose header names another message is dropped', async () => {
        cachePeerPub('bob', bob.pubB64u);
        const sealed = await side('alice').sealReaction(viewAtAlice, 'm1', '👍');
        await become(bob);
        expect(await side('bob').openReaction({ message_id: 'm2', from_webid: 'alice', ...sealed }, 'add')).toBe(null);
    });

    it('plaintext reactions from older clients and rooms are unchanged', async () => {
        const b = side('bob');
        const ev = { type: 'reaction_added', message_id: 'm1', emoji: '👍', from_webid: 'carol' };
        expect((await b.openReaction(ev, 'add')).emoji).toBe('👍');
        expect(b.translateReaction('👍')).toBe('👍');
        expect(b.translateReaction(':party:')).toBe(':party:');
        // An unknown token (never decrypted on this device) is hidden, not shown raw.
        expect(b.translateReaction('~AAAAAAAAAAA')).toBe(null);
    });
});

describe('inline files', () => {
    it('bytes, name, type and size leave only as ciphertext; the peer opens them', async () => {
        cachePeerPub('bob', bob.pubB64u);
        const a = side('alice');
        const plain = _enc.encode('PNG? no, just a very private text file');
        const meta = { name: 'payroll-2026.txt', mime: 'text/plain', size: plain.length, width: 10, height: 20, spoiler: true };
        const { hdr, ct } = await a.sealFile(viewAtAlice, plain, meta);
        expect(ct.length).toBe(plain.length + FILE_TAG_BYTES);
        const b64 = Buffer.from(ct).toString('base64');
        const cmd = { cmd: 'send_file', cert_id: 'cert-1', filename: SEALED_FILE_NAME, mime_type: SEALED_FILE_MIME, data_b64: b64, ...hdr };
        expect(hasPlain(cmd, 'payroll')).toBe(false);
        expect(hasPlain(cmd, 'text/plain')).toBe(false);
        expect(hasPlain(cmd, Buffer.from(plain).toString('base64'))).toBe(false);
        expect(cmd.width).toBeUndefined();
        // Gateway event shape (content = sealed ciphertext, placeholder file object).
        const ev = { type: 'message', from_webid: 'alice', message_id: 'file-1', content: hdr.content,
            file: { filename: 'file', mime_type: SEALED_FILE_MIME, size: ct.length, data_b64: b64 },
            e2e: true, e2e_v: 1, nonce: hdr.nonce, msg_num: hdr.msg_num, pn: hdr.pn, ratchet_pub: hdr.ratchet_pub };
        const echo = await a.openFileMessage({ ...ev });
        expect(echo.file.filename).toBe('payroll-2026.txt');

        await become(bob);
        const got = await side('bob').openFileMessage(ev);
        expect(got.content).toBe('📎 payroll-2026.txt');
        expect(got.file.mime_type).toBe('text/plain');
        expect(Buffer.from(got.file.data_b64, 'base64').toString()).toBe('PNG? no, just a very private text file');
        expect(got.file).toMatchObject({ width: 10, height: 20, spoiler: true, size: plain.length });
        expect(got._persistDm).toBe(true);
    });

    it('tampered ciphertext shows the could-not-decrypt placeholder without file data', async () => {
        cachePeerPub('bob', bob.pubB64u);
        const { hdr, ct } = await side('alice').sealFile(viewAtAlice, _enc.encode('hello'), { name: 'a.txt', mime: 'text/plain', size: 5 });
        ct[0] ^= 1;
        await become(bob);
        const got = await side('bob').openFileMessage({ type: 'message', from_webid: 'alice', ...hdr,
            file: { data_b64: Buffer.from(ct).toString('base64') } });
        expect(got.content).toBe(UNDECRYPTABLE);
        expect(got.file).toBeUndefined();
    });

    it('a decrypted type outside the allowlist is refused', async () => {
        cachePeerPub('bob', bob.pubB64u);
        const { hdr, ct } = await side('alice').sealFile(viewAtAlice, _enc.encode('<script>'), { name: 'x.html', mime: 'text/html', size: 8 });
        await become(bob);
        const got = await side('bob').openFileMessage({ type: 'message', from_webid: 'alice', ...hdr,
            file: { data_b64: Buffer.from(ct).toString('base64') } });
        expect(got.file).toBeUndefined();
    });

    it('a plaintext file from an older client is passed through', async () => {
        const ev = { type: 'message', from_webid: 'carol', content: '📎 a.png', file: { filename: 'a.png', mime_type: 'image/png', data_b64: 'AA==' } };
        expect(sealedState(ev)).toBe('legacy');
        expect(await side('bob').openFileMessage(ev)).toEqual(ev);
    });
});

describe('chunked files', () => {
    it('offer and chunks carry only ciphertext; the receiver reassembles and decrypts', async () => {
        cachePeerPub('bob', bob.pubB64u);
        const a = side('alice');
        globalThis.document = {
            getElementById: () => null,
            createElement: () => ({ style: {}, remove() {} }),
            body: { appendChild() {} },
        };
        const plain = new Uint8Array(150 * 1024);
        for (let i = 0; i < plain.length; i++) plain[i] = (i * 7) & 0xff;
        plain.set(_enc.encode('%PDF-1.7 board minutes'), 0);
        const sent = [];
        const ftA = createFileTransfer({ sendCmd: (cmd, p) => sent.push({ cmd, ...p }), showToast() {}, renderMessage() {}, getActiveView: () => viewAtAlice });
        const file = { name: 'minutes.pdf', type: 'application/pdf', arrayBuffer: async () => plain.buffer };
        const p = ftA.sendFileChunked(file, 'bob', { seal: (u8, meta) => a.sealFile(viewAtAlice, u8, meta, { echo: false }) });
        for (let i = 0; i < 200 && !sent.some(s => s.cmd === 'file_offer'); i++) await new Promise(r => setTimeout(r, 5));
        const offer = sent.find(s => s.cmd === 'file_offer');
        expect(offer.filename).toBe(SEALED_FILE_NAME);
        expect(offer.mime_type).toBe(SEALED_FILE_MIME);
        expect(offer.e2e_v).toBe(1);
        expect(hasPlain(offer, 'minutes')).toBe(false);
        ftA.handleFileAccept({ file_id: offer.file_id });
        await p;
        const chunks = sent.filter(s => s.cmd === 'file_chunk');
        expect(chunks.length).toBe(offer.total_chunks);
        const joined = Buffer.concat(chunks.map(c => Buffer.from(c.data, 'base64')));
        expect(joined.length).toBe(plain.length + FILE_TAG_BYTES);
        expect(joined.includes(Buffer.from('%PDF'))).toBe(false);

        await become(bob);
        const b = side('bob');
        let rendered = null;
        const bobSent = [];
        const ftB = createFileTransfer({ sendCmd: (cmd, p2) => bobSent.push({ cmd, ...p2 }), showToast() {}, renderMessage: (m) => { rendered = m; }, getActiveView: () => null });
        const offerEv = await b.openFileOffer({ type: 'file_offer', from_webid: 'alice', ...offer });
        expect(offerEv._sealedFile.filename).toBe('minutes.pdf');
        ftB.handleFileOffer(offerEv);
        expect(bobSent[0].cmd).toBe('file_accept');
        for (const c of chunks) ftB.handleFileChunk({ file_id: c.file_id, seq: c.seq, data: c.data });
        await ftB.handleFileComplete({ file_id: offer.file_id });
        expect(rendered.file.filename).toBe('minutes.pdf');
        expect(rendered.file.mime_type).toBe('application/pdf');
        expect(Buffer.from(rendered.file.data_b64, 'base64').equals(Buffer.from(plain))).toBe(true);
    });

    it('an offer whose sealed header cannot be opened is refused', async () => {
        const b = side('bob');
        const ev = await b.openFileOffer({ type: 'file_offer', from_webid: 'alice', file_id: 'f', e2e: true, e2e_v: 1,
            content: 'AAAA', nonce: 'AAAAAAAAAAAAAAAA', msg_num: 0, pn: 0, ratchet_pub: 'AAAA' });
        expect(ev._sealedBad).toBe(true);
        const sent = [];
        globalThis.document = { getElementById: () => null, createElement: () => ({ style: {} }), body: { appendChild() {} } };
        createFileTransfer({ sendCmd: (cmd, p) => sent.push({ cmd, ...p }), showToast() {}, renderMessage() {}, getActiveView: () => null })
            .handleFileOffer({ ...ev, size_bytes: 10, total_chunks: 1 });
        expect(sent[0]).toMatchObject({ cmd: 'file_reject', reason: 'bad_offer' });
    });
});

describe('helpers', () => {
    it('sealed plaintext is marked so the message path can refuse it', () => {
        expect(isSealedPlain(encodeSealedPlain('edit', { mid: 'm', text: 't' }))).toBe(true);
        expect(isSealedPlain('{"v":1,"k":"edit"}')).toBe(false);
    });
    it('file encryption round-trips and never starts with an executable signature', async () => {
        const u8 = _enc.encode('x'.repeat(100));
        for (let i = 0; i < 20; i++) {
            const { ct, key, iv } = await encryptFile(u8);
            expect([ct[0], ct[1]]).not.toEqual([0x4d, 0x5a]);
            expect(Buffer.from(await decryptFile(ct, key, iv)).toString()).toBe('x'.repeat(100));
        }
    });
    it('the outbox is bounded', () => {
        const o = createSealedOutbox(2);
        for (let i = 0; i < 5; i++) o.put({ ratchet_pub: 'r', msg_num: i }, i);
        expect(o.size).toBe(2);
        expect(o.take({ ratchet_pub: 'r', msg_num: 0 })).toBe(null);
        expect(o.take({ ratchet_pub: 'r', msg_num: 4 })).toBe(4);
    });
    it('edit history keeps earlier text for the local history view', () => {
        const rec = applyEditToRecord({ content: 'v1' }, 'v2', { edited_at: 't1', edited_by: 'alice' });
        applyEditToRecord(rec, 'v3', { edited_at: 't2', edited_by: 'alice' });
        expect(rec.content).toBe('v3');
        expect(rec.edits.map(e => e.prev_content)).toEqual(['v1', 'v2']);
        expect(rec.edited_at).toBe('t2');
    });
});

describe('UI modules send sealed fields for DMs', () => {
    function socket() {
        const sent = [];
        return { sent, s: { readyState: 1, send: (x) => sent.push(JSON.parse(x)) } };
    }
    it('commitEdit sends the sealed edit, not the text', async () => {
        const { sent, s } = socket();
        globalThis.document = { getElementById: () => null, createElement: () => ({}) };
        const e = createEdit({ getSocket: () => s, getActiveView: () => ({ type: 'dm', id: 'c1', peerWebid: 'bob' }),
            getClientDid: () => 'alice', getMessageMap: () => ({}),
            sealEdit: async (_v, mid, text) => ({ e2e: true, e2e_v: 1, content: 'CT', nonce: 'n', msg_num: 0, pn: 0, ratchet_pub: 'r', _t: text.length }) });
        e.commitEdit('m1', 'plain words');
        await new Promise(r => setTimeout(r, 0));
        expect(sent[0]).toMatchObject({ cmd: 'edit_message', cert_id: 'c1', content: 'CT', e2e_v: 1 });
        expect(hasPlain(sent, 'plain words')).toBe(false);
    });
    it('commitEdit sends nothing when sealing fails', async () => {
        const { sent, s } = socket();
        const failed = vi.fn();
        globalThis.document = { getElementById: () => null, createElement: () => ({}) };
        const e = createEdit({ getSocket: () => s, getActiveView: () => ({ type: 'local_dm', id: 't1' }),
            getClientDid: () => 'alice', getMessageMap: () => ({}),
            sealEdit: async () => { throw new Error('no'); }, onSealFailed: failed });
        e.commitEdit('m1', 'plain words');
        await new Promise(r => setTimeout(r, 0));
        expect(sent).toEqual([]);
        expect(failed).toHaveBeenCalled();
    });
    it('addEmoji sends the sealed token; removeReaction names it', async () => {
        const { sent, s } = socket();
        globalThis.document = { getElementById: () => ({ style: {} }), contains: () => false, activeElement: null };
        const r = createReactions({ getSocket: () => s, getActiveView: () => ({ type: 'dm', id: 'c1' }),
            getSelfWebId: () => 'alice', getMessageReactions: () => ({}),
            sealReaction: async () => ({ emoji: '~AbCdEfGhIjK', e2e: true, e2e_v: 1, content: 'CT' }),
            wireEmoji: () => '~AbCdEfGhIjK' });
        await r.addEmoji('🔥', 'm1');
        r.removeReaction('🔥', 'm1');
        expect(sent[0]).toMatchObject({ cmd: 'add_reaction', emoji: '~AbCdEfGhIjK', e2e_v: 1 });
        expect(sent[1]).toMatchObject({ cmd: 'remove_reaction', emoji: '~AbCdEfGhIjK' });
        expect(hasPlain(sent, '🔥')).toBe(false);
    });
    it('rooms keep plaintext reactions', async () => {
        const { sent, s } = socket();
        globalThis.document = { getElementById: () => ({ style: {} }), contains: () => false, activeElement: null };
        const r = createReactions({ getSocket: () => s, getActiveView: () => ({ type: 'local_room', id: 'r1' }),
            getSelfWebId: () => 'alice', getMessageReactions: () => ({}),
            sealReaction: async () => { throw new Error('must not be called for rooms'); } });
        r.addEmoji('🔥', 'm1');
        expect(sent[0]).toMatchObject({ cmd: 'add_reaction', room_id: 'r1', emoji: '🔥' });
    });
});
