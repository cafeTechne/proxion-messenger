// longchatsig.test.js — signing & verifying SolidOS Long Chat room messages (#4).
//
// A Long Chat message carries one sec:proofValue literal per the Solid chat SHACL
// shape: an Ed25519 signature over the message's core fields (id, created,
// content, maker), packed as "<did:key>|<b64sig>". These lock the round trip, the
// tamper failures, the canonical framing against an independent vector, and the
// read path where an unsigned foreign message stays unverified but is never
// dropped. Mirrors dmsig.test.js.
import { describe, it, expect } from 'vitest';
import {
    canonicalLongChatBytes, signLongChat, verifyLongChatProof,
} from './dmsig.js';
import {
    appendOps, parseLongChatJsonLd, verifyLongChatMessages, P,
} from './longchat.js';

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function b58encode(bytes) {
    let zeros = 0; while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
    const digits = [0];
    for (let i = zeros; i < bytes.length; i++) {
        let carry = bytes[i];
        for (let j = 0; j < digits.length; j++) { carry += digits[j] << 8; digits[j] = carry % 58; carry = (carry / 58) | 0; }
        while (carry > 0) { digits.push(carry % 58); carry = (carry / 58) | 0; }
    }
    let s = ''; for (let k = 0; k < zeros; k++) s += '1';
    for (let q = digits.length - 1; q >= 0; q--) s += B58[digits[q]];
    return s;
}
function didFromPub(pub) {
    const mc = new Uint8Array(2 + pub.length); mc[0] = 0xed; mc[1] = 0x01; mc.set(pub, 2);
    return 'did:key:z' + b58encode(mc);
}
async function makeIdentity() {
    const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
    const rawPub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
    return { priv: kp.privateKey, did: didFromPub(rawPub) };
}

// The signed core fields, exactly as the SHACL shape names them.
const core = {
    id: 'https://alice.pod/proxion/rooms/general/2026/08/25/chat.ttl#m1',
    created: '2026-08-25T10:00:00.000Z',
    content: 'Morning, everyone',
    maker: 'https://alice.pod/profile/card#me',
};

describe('longchat message signing (dmsig primitives)', () => {
    it('round-trips: a signed message verifies and recovers the signer key', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        // Proof format: the did:key and the base64 signature, split by a single '|'.
        expect(proof.startsWith(id.did + '|')).toBe(true);
        expect(await verifyLongChatProof(core, proof)).toBe(id.did);
    });

    it('rejects tampered content', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        expect(await verifyLongChatProof({ ...core, content: 'TAMPERED' }, proof)).toBe(null);
    });

    it('rejects a swapped maker (the author-spoofing case)', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        expect(await verifyLongChatProof({ ...core, maker: 'https://mallory.pod/profile/card#me' }, proof)).toBe(null);
    });

    it('rejects a tampered id or created time', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        expect(await verifyLongChatProof({ ...core, id: core.id + 'x' }, proof)).toBe(null);
        expect(await verifyLongChatProof({ ...core, created: '2026-08-25T10:00:00.001Z' }, proof)).toBe(null);
    });

    it('rejects a proof signed by a different key than its embedded did', async () => {
        const id = await makeIdentity();
        const other = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        // Repoint the embedded did to a key that did not sign these bytes.
        const forged = other.did + '|' + proof.slice(proof.indexOf('|') + 1);
        expect(await verifyLongChatProof(core, forged)).toBe(null);
    });

    it('returns null for a missing or malformed proof', async () => {
        expect(await verifyLongChatProof(core, null)).toBe(null);
        expect(await verifyLongChatProof(core, 'no-delimiter')).toBe(null);
        expect(await verifyLongChatProof(core, 'did:key:zbad|')).toBe(null);
    });

    it('signLongChat returns null without a key', async () => {
        expect(await signLongChat(core, null, 'did:key:zabc')).toBe(null);
    });
});

// Independent re-implementation of the 4-byte-length-prefixed, 0x7c-joined framing
// over exactly [id, created, content, maker]. Equality with canonicalLongChatBytes
// is the known vector: it proves the canonicalizer matches the documented shape.
const _ENC = new TextEncoder();
function vectorCanonical(obj) {
    const fields = ['id', 'created', 'content', 'maker'];
    const parts = fields.map((k) => _ENC.encode(obj[k] != null ? String(obj[k]) : ''));
    const chunks = parts.map((p) => {
        const c = new Uint8Array(4 + p.length);
        c[0] = (p.length >> 24) & 0xff; c[1] = (p.length >> 16) & 0xff;
        c[2] = (p.length >> 8) & 0xff; c[3] = p.length & 0xff; c.set(p, 4);
        return c;
    });
    const total = chunks.reduce((a, c) => a + c.length, 0) + (chunks.length - 1);
    const out = new Uint8Array(total);
    let off = 0;
    chunks.forEach((c, i) => { if (i > 0) out[off++] = 0x7c; out.set(c, off); off += c.length; });
    return out;
}

describe('canonicalLongChatBytes framing', () => {
    it('matches an independent implementation of the shape framing (known vector)', () => {
        expect(Array.from(canonicalLongChatBytes(core))).toEqual(Array.from(vectorCanonical(core)));
    });

    it('is deterministic and any core field changes the bytes', () => {
        expect(Array.from(canonicalLongChatBytes(core))).toEqual(Array.from(canonicalLongChatBytes({ ...core })));
        for (const k of ['id', 'created', 'content', 'maker']) {
            expect(Array.from(canonicalLongChatBytes(core)))
                .not.toEqual(Array.from(canonicalLongChatBytes({ ...core, [k]: core[k] + '!' })));
        }
    });
});

describe('appendOps sec:proofValue', () => {
    const base = {
        channelIri: 'https://alice.pod/proxion/rooms/general/index.ttl#this',
        messageIri: core.id, content: core.content, createdIso: core.created, makerIri: core.maker,
    };
    it('emits the proof triple when a proof is supplied', () => {
        const { inserts } = appendOps({ ...base, proof: 'did:key:zAlice|SIG' });
        expect(inserts.some(t => t.includes(`<${P.proofValue}> "did:key:zAlice|SIG"`))).toBe(true);
    });
    it('omits the proof triple when none is supplied (unsigned, still valid)', () => {
        const { inserts } = appendOps(base);
        expect(inserts.some(t => t.includes(P.proofValue))).toBe(false);
    });
});

// A day-file node in expanded JSON-LD, optionally carrying the proof literal.
function msgNode(proof) {
    return {
        '@id': core.id,
        [P.content]: [{ '@value': core.content }],
        [P.created]: [{ '@value': core.created, '@type': P.dateTime }],
        [P.maker]: [{ '@id': core.maker }],
        ...(proof ? { [P.proofValue]: [{ '@value': proof }] } : {}),
    };
}

const ALICE_ROOT = 'https://alice.pod/';
const peerPodRoot = () => ALICE_ROOT;

describe('verifyLongChatMessages (read-path authorization)', () => {
    it('marks a message verified when the signature is valid AND the maker published the signer', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        const msgs = parseLongChatJsonLd([msgNode(proof)], 'general');
        expect(msgs[0].sender_verified).toBe(false);   // parse leaves it unverified
        const fetchPeerSigner = async () => ({ signer: id.did });
        await verifyLongChatMessages(msgs, { fetchPeerSigner, peerPodRoot });
        expect(msgs[0].sender_verified).toBe(true);
    });

    it('stays unverified when the maker published a DIFFERENT signer (unauthorized)', async () => {
        const id = await makeIdentity();
        const other = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        const msgs = parseLongChatJsonLd([msgNode(proof)], 'general');
        const fetchPeerSigner = async () => ({ signer: other.did });
        await verifyLongChatMessages(msgs, { fetchPeerSigner, peerPodRoot });
        expect(msgs[0].sender_verified).toBe(false);
    });

    it('stays unverified when the maker has not published a signer at all', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        const msgs = parseLongChatJsonLd([msgNode(proof)], 'general');
        const fetchPeerSigner = async () => null;
        await verifyLongChatMessages(msgs, { fetchPeerSigner, peerPodRoot });
        expect(msgs[0].sender_verified).toBe(false);
    });

    it('stays unverified when the content was edited after signing (proof no longer matches)', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        const node = msgNode(proof);
        node[P.content] = [{ '@value': 'edited-in-place' }];
        const msgs = parseLongChatJsonLd([node], 'general');
        const fetchPeerSigner = async () => ({ signer: id.did });
        await verifyLongChatMessages(msgs, { fetchPeerSigner, peerPodRoot });
        expect(msgs[0].sender_verified).toBe(false);
    });
});

describe('unsigned / foreign Long Chat messages (interop)', () => {
    it('parses an unsigned foreign message as unverified but still returns it (never dropped)', async () => {
        const msgs = parseLongChatJsonLd([msgNode(null)], 'general');
        expect(msgs).toHaveLength(1);
        expect(msgs[0].content).toBe(core.content);
        expect(msgs[0].proof).toBe(null);
        expect(msgs[0].sender_verified).toBe(false);
        // Running verification changes nothing for a proof-less message; it stays.
        await verifyLongChatMessages(msgs, { fetchPeerSigner: async () => ({ signer: 'x' }), peerPodRoot });
        expect(msgs).toHaveLength(1);
        expect(msgs[0].sender_verified).toBe(false);
    });

    it('is a no-op without injected verification deps (offline / no pod)', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        const msgs = parseLongChatJsonLd([msgNode(proof)], 'general');
        await verifyLongChatMessages(msgs, {});
        expect(msgs[0].sender_verified).toBe(false);
    });
});
