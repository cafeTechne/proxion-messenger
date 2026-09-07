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
    canonicalLongChatBytes, signLongChat, verifyLongChatProof, signLongChatDelete,
} from './dmsig.js';
import {
    appendOps, deleteOps, parseLongChatJsonLd, verifyLongChatMessages, P,
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

// The signed fields: the four SHACL core fields plus the author's display name
// (Part B), so a validly-signed message cannot carry a name spoofing someone else.
const core = {
    id: 'https://alice.pod/proxion/rooms/general/2026/08/25/chat.ttl#m1',
    created: '2026-08-25T10:00:00.000Z',
    content: 'Morning, everyone',
    maker: 'https://alice.pod/profile/card#me',
    from_display_name: 'Alice',
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

    it('rejects a spoofed display name (Part B: the name is signed)', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);
        // Same signer, same maker, but the name is changed after signing.
        expect(await verifyLongChatProof({ ...core, from_display_name: 'Bob' }, proof)).toBe(null);
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
function framedCanonical(fields, obj) {
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
// The current signed set: the four core fields plus from_display_name.
const vectorCanonical = (obj) => framedCanonical(['id', 'created', 'content', 'maker', 'from_display_name'], obj);
// The pre-name (legacy) set the deprecation shim in verify still accepts.
const LEGACY_FIELDS = ['id', 'created', 'content', 'maker'];

describe('canonicalLongChatBytes framing', () => {
    it('matches an independent implementation of the shape framing (known vector)', () => {
        expect(Array.from(canonicalLongChatBytes(core))).toEqual(Array.from(vectorCanonical(core)));
    });

    it('is deterministic and any signed field changes the bytes', () => {
        expect(Array.from(canonicalLongChatBytes(core))).toEqual(Array.from(canonicalLongChatBytes({ ...core })));
        for (const k of ['id', 'created', 'content', 'maker', 'from_display_name']) {
            expect(Array.from(canonicalLongChatBytes(core)))
                .not.toEqual(Array.from(canonicalLongChatBytes({ ...core, [k]: core[k] + '!' })));
        }
    });
});

describe('legacy-signature shim (pre-name field set)', () => {
    // A message signed by the just-shipped code covered only the four core fields
    // and never wrote a name. The shim must still verify it, but ONLY when no name
    // is present — a name has to be covered by the current-scheme signature.
    const legacyCore = { id: core.id, created: core.created, content: core.content, maker: core.maker };
    async function legacyProof(id) {
        const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', id.priv, framedCanonical(LEGACY_FIELDS, legacyCore)));
        return id.did + '|' + _b64FromBytes(sig);
    }
    function _b64FromBytes(bytes) { let s = ''; for (const b of bytes) s += String.fromCharCode(b); return btoa(s); }

    it('accepts a legacy (four-field) signature when there is no display name', async () => {
        const id = await makeIdentity();
        const proof = await legacyProof(id);
        expect(await verifyLongChatProof(legacyCore, proof)).toBe(id.did);
        expect(await verifyLongChatProof({ ...legacyCore, from_display_name: '' }, proof)).toBe(id.did);
    });

    it('refuses to bless an Appended name on a legacy-signed message (spoof defeated)', async () => {
        const id = await makeIdentity();
        const proof = await legacyProof(id);
        // Attacker appends px:fromName onto a legacy message; the shim must NOT accept it.
        expect(await verifyLongChatProof({ ...legacyCore, from_display_name: 'Alice' }, proof)).toBe(null);
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

// A day-file node in expanded JSON-LD, optionally carrying the proof literal. The
// px:fromName mirrors core.from_display_name so the parsed message matches the
// signed material (Part B: the name is part of the signed set).
function msgNode(proof) {
    return {
        '@id': core.id,
        [P.content]: [{ '@value': core.content }],
        [P.created]: [{ '@value': core.created, '@type': P.dateTime }],
        [P.maker]: [{ '@id': core.maker }],
        [P.fromName]: [{ '@value': core.from_display_name }],
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

    it('stays unverified when the display name was Appended/spoofed after signing (Part B)', async () => {
        const id = await makeIdentity();
        const proof = await signLongChat(core, id.priv, id.did);   // signs name "Alice"
        const node = msgNode(proof);
        node[P.fromName] = [{ '@value': 'Mallory' }];              // name swapped in the day file
        const msgs = parseLongChatJsonLd([node], 'general');
        expect(msgs[0].from_display_name).toBe('Mallory');
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

// ── Part A: authenticated soft-delete (the censorship HIGH) ──────────────────
describe('authenticated soft-delete (Part A)', () => {
    const MSG_IRI = core.id;
    const MAKER = core.maker;                                    // https://alice.pod/...
    const OWNER = 'https://owner.pod/profile/card#me';
    const DEL_ISO = '2026-08-25T12:00:00.000Z';

    // A distinct pod root per WebID so their published signers do not collide, and
    // deps that resolve each WebID's own published signer did:key.
    const rootOf = (w) => `https://${new URL(w).host}/`;
    function deps(published, extra = {}) {
        const byRoot = {};
        for (const [w, did] of Object.entries(published)) byRoot[rootOf(w)] = { signer: did };
        return { peerPodRoot: rootOf, fetchPeerSigner: async (root) => byRoot[root] || null, ...extra };
    }

    function tombstoneNode(deleteProof) {
        return {
            '@id': MSG_IRI,
            [P.content]: [{ '@value': core.content }],
            [P.created]: [{ '@value': core.created, '@type': P.dateTime }],
            [P.maker]: [{ '@id': MAKER }],
            [P.fromName]: [{ '@value': core.from_display_name }],
            [P.dateDeleted]: [{ '@value': DEL_ISO, '@type': P.dateTime }],
            ...(deleteProof ? { [P.deleteProof]: [{ '@value': deleteProof }] } : {}),
        };
    }

    it('parse leaves a tombstoned message VISIBLE (raw tombstone not honoured)', () => {
        const [m] = parseLongChatJsonLd([tombstoneNode(null)], 'general');
        expect(m.deleted).toBe(false);
        expect(m.content).toBe(core.content);
        expect(m.deleted_at).toBe(DEL_ISO);           // extracted, awaiting authentication
    });

    it('a forged tombstone by a NON-maker does NOT hide the message (closes the HIGH)', async () => {
        const maker = await makeIdentity();
        const attacker = await makeIdentity();
        const proof = await signLongChatDelete({ iri: MSG_IRI, deletedIso: DEL_ISO }, attacker.priv, attacker.did);
        const msgs = parseLongChatJsonLd([tombstoneNode(proof)], 'general');
        await verifyLongChatMessages(msgs, deps({ [MAKER]: maker.did }));
        expect(msgs[0].deleted).toBe(false);
        expect(msgs[0].content).toBe(core.content);
    });

    it('an authenticated self-delete (maker-signed) hides the message', async () => {
        const maker = await makeIdentity();
        const proof = await signLongChatDelete({ iri: MSG_IRI, deletedIso: DEL_ISO }, maker.priv, maker.did);
        const msgs = parseLongChatJsonLd([tombstoneNode(proof)], 'general');
        await verifyLongChatMessages(msgs, deps({ [MAKER]: maker.did }));
        expect(msgs[0].deleted).toBe(true);
        expect(msgs[0].content).toBe('');
    });

    it('honours an owner-moderation delete only when ownerWebId is supplied', async () => {
        const maker = await makeIdentity();
        const owner = await makeIdentity();
        const proof = await signLongChatDelete({ iri: MSG_IRI, deletedIso: DEL_ISO }, owner.priv, owner.did);
        const published = { [MAKER]: maker.did, [OWNER]: owner.did };
        // No owner authorization: the owner's delete of a member's message is ignored.
        const a = parseLongChatJsonLd([tombstoneNode(proof)], 'general');
        await verifyLongChatMessages(a, deps(published));
        expect(a[0].deleted).toBe(false);
        // With the room owner supplied, it authenticates.
        const b = parseLongChatJsonLd([tombstoneNode(proof)], 'general');
        await verifyLongChatMessages(b, deps(published, { ownerWebId: OWNER }));
        expect(b[0].deleted).toBe(true);
        expect(b[0].content).toBe('');
    });

    it('deleteOps emits px:deleteProof alongside the tombstone when signed', () => {
        const { inserts } = deleteOps({ messageIri: MSG_IRI, deletedIso: DEL_ISO, proof: 'did:key:zX|SIG' });
        expect(inserts.some(t => t.includes(`<${P.deleteProof}> "did:key:zX|SIG"`))).toBe(true);
        expect(inserts.some(t => t.includes(P.dateDeleted))).toBe(true);
    });
});
