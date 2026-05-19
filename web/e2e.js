/**
 * web/e2e.js — Double Ratchet E2E encryption for Proxion DMs (Phase 2)
 *
 * Key exchange: X25519 ECDH. Each direction switch rotates the DH ratchet key,
 * re-derives the root key, and starts a new symmetric chain (break-in recovery).
 * State: AES-256-GCM encrypted, stored to pod (primary) + localStorage (fast cache).
 *
 * Phase 1 states (no rootKey field) are silently discarded on load.
 */
import { solidSession, podStorageRoot } from './auth.js';

export class E2EDecryptError extends Error {
    constructor(msg) { super(msg); this.name = 'E2EDecryptError'; }
}

const _ENC = new TextEncoder();
const _DEC = new TextDecoder();
export const MAX_SKIP = 20;

export let e2eSupported = false;

let _myPrivKey   = null;  // CryptoKey (X25519, non-extractable, deriveBits)
let _myPrivJwk   = null;  // JWK { kty, crv, x, d }
let _myPubB64u   = null;  // base64url-encoded 32-byte X25519 public key
let _stateEncKey = null;  // CryptoKey AES-256-GCM (for encrypting ratchet state)
let _initDone    = false;
let _initPromise = null;
const _stateCache = {};   // peerId -> state (in-memory, cleared on reset)

// ── Encoding helpers (exported for tests) ─────────────────────────────────────
export function b64uEnc(bytes) {
    return btoa(String.fromCharCode(...bytes))
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64uDec(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    return Uint8Array.from(atob(s), c => c.charCodeAt(0));
}

// ── Crypto primitives (exported for unit tests) ───────────────────────────────

export async function hmac(keyBytes, data) {
    const k = await crypto.subtle.importKey(
        'raw', keyBytes, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    return new Uint8Array(await crypto.subtle.sign('HMAC', k, data));
}

export async function hkdf(ikm, salt, info, bits) {
    const k = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
    return new Uint8Array(await crypto.subtle.deriveBits(
        { name: 'HKDF', hash: 'SHA-256',
          salt: _ENC.encode(salt), info: _ENC.encode(info) },
        k, bits));
}

// Advance the symmetric chain ratchet one step.
// Returns { msgKey: Uint8Array, nextKey: Uint8Array }.
export async function advanceChain(chainKeyBytes) {
    const msgKey  = await hmac(chainKeyBytes, new Uint8Array([0x01]));
    const nextKey = await hmac(chainKeyBytes, new Uint8Array([0x02]));
    return { msgKey, nextKey };
}

// Derive root key + initial chain key from raw DH output bytes (Phase 1 utility).
export async function deriveRootAndChain(dhBits) {
    const out = await hkdf(dhBits, 'proxion-dm-v1', 'root', 512);
    return { rootKey: out.slice(0, 32), chainKey: out.slice(32) };
}

// ── Phase 2: DH ratchet key derivation ───────────────────────────────────────
//
// KDF_RK(rootKey, dhBits) → { newRootKey, chainKey }
//   HKDF-SHA256(ikm=dhBits, salt=rootKey, info='ratchet') → 64 bytes
//   new root key = first 32 bytes; new chain key = last 32 bytes.
//
export async function kdfRk(rootKey, dhBits) {
    const k = await crypto.subtle.importKey('raw', dhBits, 'HKDF', false, ['deriveBits']);
    const out = new Uint8Array(await crypto.subtle.deriveBits(
        { name: 'HKDF', hash: 'SHA-256',
          salt: new Uint8Array(rootKey),
          info: _ENC.encode('ratchet') },
        k, 512));
    return { newRootKey: out.slice(0, 32), chainKey: out.slice(32) };
}

async function _aesKey(raw) {
    return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
}

// ── Safety number ─────────────────────────────────────────────────────────────
//
// SHA-256(sort([myPub, peerPub]).join('|')) → 6 groups of 5 decimal digits.
// Symmetric: safetyNumber(a, b) === safetyNumber(b, a).
//
export async function safetyNumber(myPubB64u, peerPubB64u) {
    const sorted = [myPubB64u, peerPubB64u].sort();
    const digest = new Uint8Array(await crypto.subtle.digest(
        'SHA-256', _ENC.encode(sorted[0] + '|' + sorted[1])));
    const groups = [];
    for (let i = 0; i < 6; i++) {
        const v = ((digest[i * 4] << 24) | (digest[i * 4 + 1] << 16) |
                   (digest[i * 4 + 2] << 8) | digest[i * 4 + 3]) >>> 0;
        groups.push(String(v % 100000).padStart(5, '0'));
    }
    return groups.join(' ');
}

// ── Init ──────────────────────────────────────────────────────────────────────

export async function initE2E() {
    if (_initDone) return;
    if (_initPromise) return _initPromise;
    _initPromise = _doInit().catch(err => {
        console.warn('[e2e] init failed, E2E disabled:', err);
    });
    return _initPromise;
}

async function _doInit() {
    try {
        void await crypto.subtle.generateKey(
            { name: 'X25519' }, false, ['deriveBits']);
    } catch {
        return;
    }

    const savedJwk = localStorage.getItem('proxion_e2e_x25519_priv_jwk');
    const savedPub = localStorage.getItem('proxion_e2e_x25519_pub_b64u');
    let loaded = false;

    if (savedJwk && savedPub) {
        try {
            const jwk = JSON.parse(savedJwk);
            _myPrivKey = await crypto.subtle.importKey(
                'jwk', jwk, { name: 'X25519' }, false, ['deriveBits']);
            _myPrivJwk = jwk;
            _myPubB64u = savedPub;
            loaded = true;
        } catch {
            localStorage.removeItem('proxion_e2e_x25519_priv_jwk');
            localStorage.removeItem('proxion_e2e_x25519_pub_b64u');
        }
    }

    if (!loaded) {
        const kp = await crypto.subtle.generateKey(
            { name: 'X25519' }, true, ['deriveBits']);
        const privJwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
        const pubJwk  = await crypto.subtle.exportKey('jwk', kp.publicKey);
        _myPrivJwk = privJwk;
        _myPubB64u = pubJwk.x;
        _myPrivKey = await crypto.subtle.importKey(
            'jwk', privJwk, { name: 'X25519' }, false, ['deriveBits']);
        localStorage.setItem('proxion_e2e_x25519_priv_jwk', JSON.stringify(privJwk));
        localStorage.setItem('proxion_e2e_x25519_pub_b64u', _myPubB64u);
    }

    const stateKeyRaw = await hkdf(b64uDec(_myPrivJwk.d), 'proxion-e2e-state-v1', '', 256);
    _stateEncKey = await _aesKey(stateKeyRaw);

    _initDone = true;
    e2eSupported = true;

    _publishPubToPod().catch(() => {});
}

async function _publishPubToPod() {
    const root = typeof podStorageRoot === 'function' ? podStorageRoot() : null;
    if (!root || !solidSession?.info?.isLoggedIn) return;
    await solidSession.fetch(root + 'proxion/identity/x25519-pub.json', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: 1, pub: _myPubB64u }),
    });
}

// ── Peer key management ───────────────────────────────────────────────────────

export function myX25519PubB64u() { return _myPubB64u; }

export function cachePeerPub(peerId, pubB64u) {
    if (!peerId || !pubB64u || typeof pubB64u !== 'string') return;
    localStorage.setItem('proxion_e2e_peer_pub_' + peerId, pubB64u);
}

function _peerPub(peerId) {
    return localStorage.getItem('proxion_e2e_peer_pub_' + peerId) || null;
}

export function isE2EEnabled(peerId) {
    return e2eSupported && !!_peerPub(peerId);
}

export async function fetchAndCachePeerPub(peerId, peerPodRoot) {
    if (!peerPodRoot || !solidSession?.info?.isLoggedIn) return false;
    try {
        const res = await solidSession.fetch(peerPodRoot + 'proxion/identity/x25519-pub.json');
        if (!res.ok) return false;
        const data = await res.json();
        if (data?.version === 1 && typeof data.pub === 'string') {
            cachePeerPub(peerId, data.pub);
            return true;
        }
    } catch {}
    return false;
}

// ── State persistence (pod-first + localStorage cache) ────────────────────────
//
// State shape (v2 — Phase 2 Double Ratchet):
//   rootKey:          number[]        32-byte current root key
//   myRatchetPrivJwk: object          JWK — current ratchet private key (rotates on send after recv)
//   myRatchetPub:     string          b64u — sent as "ratchet_pub" on every outgoing message
//   theirRatchetPub:  string|null     b64u — last received ratchet pub from peer
//   pendingDhRatchet: boolean         true after recv new ratchet pub; cleared on next send
//   sendChain:        number[]|null   32-byte send chain key
//   sendMsgNum:       number          next message number to send in current chain
//   sendPrevLen:      number          PN — messages sent in previous chain (for peer's skip buffer)
//   recvChain:        number[]|null   32-byte recv chain key
//   recvMsgNum:       number          next expected recv message number in current chain
//   skippedKeys:      { [k]: string } skip buffer keyed "ratchetPub:msgNum" → b64u msgKey
//
// Phase 1 states (no rootKey) are discarded on load → clean re-init.

async function _encState(state) {
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const ct = new Uint8Array(await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: nonce },
        _stateEncKey,
        _ENC.encode(JSON.stringify(state))));
    return JSON.stringify({ nonce: b64uEnc(nonce), ct: b64uEnc(ct) });
}

async function _decState(enc) {
    const { nonce, ct } = JSON.parse(enc);
    const plain = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: b64uDec(nonce) },
        _stateEncKey,
        b64uDec(ct));
    return JSON.parse(_DEC.decode(plain));
}

export async function loadRatchetState(peerId) {
    if (_stateCache[peerId]) {
        const s = _stateCache[peerId];
        if (!s.rootKey) { delete _stateCache[peerId]; return null; } // Phase 1 migration
        return s;
    }

    const root = typeof podStorageRoot === 'function' ? podStorageRoot() : null;
    if (root && solidSession?.info?.isLoggedIn) {
        try {
            const url = root + 'proxion/e2e/' + encodeURIComponent(peerId) + '/state.enc';
            const res = await solidSession.fetch(url);
            if (res.ok) {
                const state = await _decState(await res.text());
                if (!state.rootKey) return null; // Phase 1 migration
                _stateCache[peerId] = state;
                return state;
            }
        } catch {}
    }

    const ls = localStorage.getItem('proxion_e2e_state_' + peerId);
    if (ls) {
        try {
            const state = await _decState(ls);
            if (!state.rootKey) return null; // Phase 1 migration
            _stateCache[peerId] = state;
            return state;
        } catch {}
    }

    return null;
}

export async function saveRatchetState(peerId, state) {
    _stateCache[peerId] = state;
    if (!_stateEncKey) return;
    const enc = await _encState(state);
    localStorage.setItem('proxion_e2e_state_' + peerId, enc);

    const root = typeof podStorageRoot === 'function' ? podStorageRoot() : null;
    if (root && solidSession?.info?.isLoggedIn) {
        const url = root + 'proxion/e2e/' + encodeURIComponent(peerId) + '/state.enc';
        solidSession.fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: enc,
        }).catch(() => {});
    }
}

// ── Internal DH ratchet helpers ───────────────────────────────────────────────

const ZERO_ROOT = new Uint8Array(32);

async function _initSenderState(peerPubB64u) {
    const ratchetKp = await crypto.subtle.generateKey(
        { name: 'X25519' }, true, ['deriveBits']);
    const ratchetPubJwk = await crypto.subtle.exportKey('jwk', ratchetKp.publicKey);
    const ratchetPrivJwk = await crypto.subtle.exportKey('jwk', ratchetKp.privateKey);

    const peerKey = await crypto.subtle.importKey(
        'raw', b64uDec(peerPubB64u), { name: 'X25519' }, false, []);
    const dhBits = new Uint8Array(await crypto.subtle.deriveBits(
        { name: 'X25519', public: peerKey }, ratchetKp.privateKey, 256));

    const { newRootKey: rootKey, chainKey: sendChain } = await kdfRk(ZERO_ROOT, dhBits);

    return {
        rootKey: Array.from(rootKey),
        myRatchetPrivJwk: ratchetPrivJwk,
        myRatchetPub: ratchetPubJwk.x,
        theirRatchetPub: peerPubB64u,
        pendingDhRatchet: false,
        sendChain: Array.from(sendChain),
        sendMsgNum: 0,
        sendPrevLen: 0,
        recvChain: null,
        recvMsgNum: 0,
        skippedKeys: {},
    };
}

async function _initReceiverState(senderRatchetPubB64u) {
    const senderRatchetKey = await crypto.subtle.importKey(
        'raw', b64uDec(senderRatchetPubB64u), { name: 'X25519' }, false, []);

    // Derive initial recv chain (symmetric with sender's send chain)
    const dhBits = new Uint8Array(await crypto.subtle.deriveBits(
        { name: 'X25519', public: senderRatchetKey }, _myPrivKey, 256));
    const { newRootKey: rootKey, chainKey: recvChain } = await kdfRk(ZERO_ROOT, dhBits);

    // Pre-derive send chain: generate own ratchet keypair + DH ratchet step
    const myRatchetKp = await crypto.subtle.generateKey(
        { name: 'X25519' }, true, ['deriveBits']);
    const myRatchetPubJwk = await crypto.subtle.exportKey('jwk', myRatchetKp.publicKey);
    const myRatchetPrivJwk = await crypto.subtle.exportKey('jwk', myRatchetKp.privateKey);

    const dhBits2 = new Uint8Array(await crypto.subtle.deriveBits(
        { name: 'X25519', public: senderRatchetKey }, myRatchetKp.privateKey, 256));
    const { newRootKey: newRoot, chainKey: sendChain } = await kdfRk(rootKey, dhBits2);

    return {
        rootKey: Array.from(newRoot),
        myRatchetPrivJwk,
        myRatchetPub: myRatchetPubJwk.x,
        theirRatchetPub: senderRatchetPubB64u,
        pendingDhRatchet: false,
        sendChain: Array.from(sendChain),
        sendMsgNum: 0,
        sendPrevLen: 0,
        recvChain: Array.from(recvChain),
        recvMsgNum: 0,
        skippedKeys: {},
    };
}

// Buffer skipped keys from current recv chain up to `upTo` and apply DH ratchet step.
async function _dhRatchetReceive(state, newRatchetPubB64u, pn) {
    // Advance current recv chain to pn, buffering skipped keys
    let recvChain = new Uint8Array(state.recvChain);
    while (state.recvMsgNum < pn) {
        const { msgKey: sk, nextKey } = await advanceChain(recvChain);
        state.skippedKeys[state.theirRatchetPub + ':' + state.recvMsgNum] = b64uEnc(sk);
        recvChain = nextKey;
        state.recvMsgNum++;
    }

    const newRatchetKey = await crypto.subtle.importKey(
        'raw', b64uDec(newRatchetPubB64u), { name: 'X25519' }, false, []);
    const myRatchetPriv = await crypto.subtle.importKey(
        'jwk', state.myRatchetPrivJwk, { name: 'X25519' }, false, ['deriveBits']);
    const dhBits = new Uint8Array(await crypto.subtle.deriveBits(
        { name: 'X25519', public: newRatchetKey }, myRatchetPriv, 256));

    const { newRootKey, chainKey: newRecvChain } = await kdfRk(
        new Uint8Array(state.rootKey), dhBits);

    state.rootKey = Array.from(newRootKey);
    state.recvChain = Array.from(newRecvChain);
    state.recvMsgNum = 0;
    state.theirRatchetPub = newRatchetPubB64u;
    state.pendingDhRatchet = true;
    return state;
}

// Generate new ratchet keypair and advance send chain.
async function _dhRatchetSend(state) {
    const newKp = await crypto.subtle.generateKey(
        { name: 'X25519' }, true, ['deriveBits']);
    const newPubJwk  = await crypto.subtle.exportKey('jwk', newKp.publicKey);
    const newPrivJwk = await crypto.subtle.exportKey('jwk', newKp.privateKey);

    const theirKey = await crypto.subtle.importKey(
        'raw', b64uDec(state.theirRatchetPub), { name: 'X25519' }, false, []);
    const dhBits = new Uint8Array(await crypto.subtle.deriveBits(
        { name: 'X25519', public: theirKey }, newKp.privateKey, 256));

    const { newRootKey, chainKey: newSendChain } = await kdfRk(
        new Uint8Array(state.rootKey), dhBits);

    state.sendPrevLen = state.sendMsgNum;
    state.sendChain = Array.from(newSendChain);
    state.sendMsgNum = 0;
    state.rootKey = Array.from(newRootKey);
    state.myRatchetPrivJwk = newPrivJwk;
    state.myRatchetPub = newPubJwk.x;
    state.pendingDhRatchet = false;
    return state;
}

// ── ratchetEncrypt ────────────────────────────────────────────────────────────

export async function ratchetEncrypt(peerId, plaintext) {
    if (!_initDone) await initE2E();
    if (!e2eSupported) throw new Error('E2E not supported in this browser');

    let state = await loadRatchetState(peerId);

    if (!state) {
        const peerPubB64u = _peerPub(peerId);
        if (!peerPubB64u) throw new Error('No E2E pubkey cached for peer: ' + peerId);
        state = await _initSenderState(peerPubB64u);
    } else if (state.pendingDhRatchet) {
        state = await _dhRatchetSend(state);
    }

    const msgNum     = state.sendMsgNum;
    const pn         = state.sendPrevLen;
    const ratchetPub = state.myRatchetPub;

    const { msgKey, nextKey } = await advanceChain(new Uint8Array(state.sendChain));
    state.sendChain = Array.from(nextKey);
    state.sendMsgNum++;

    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const ct = new Uint8Array(await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: nonce },
        await _aesKey(msgKey),
        _ENC.encode(plaintext)));

    await saveRatchetState(peerId, state);
    return { ciphertext: b64uEnc(ct), nonce: b64uEnc(nonce), msgNum, pn, ratchetPub };
}

// ── ratchetDecrypt ────────────────────────────────────────────────────────────

export async function ratchetDecrypt(peerId, ciphertextB64u, nonceB64u, msgNum, ratchetPubB64u, pn = 0) {
    if (!_initDone) await initE2E();
    if (!e2eSupported) throw new E2EDecryptError('E2E not supported');
    if (!ratchetPubB64u) throw new E2EDecryptError('ratchet_pub required');

    let state = await loadRatchetState(peerId);

    if (!state) {
        state = await _initReceiverState(ratchetPubB64u);
    } else if (ratchetPubB64u !== state.theirRatchetPub) {
        state = await _dhRatchetReceive(state, ratchetPubB64u, pn);
    }

    const skipKey = ratchetPubB64u + ':' + msgNum;
    let msgKey;

    if (Object.prototype.hasOwnProperty.call(state.skippedKeys, skipKey)) {
        msgKey = b64uDec(state.skippedKeys[skipKey]);
        delete state.skippedKeys[skipKey];
    } else if (msgNum < state.recvMsgNum) {
        throw new E2EDecryptError('Message already consumed: msgNum=' + msgNum);
    } else {
        while (state.recvMsgNum < msgNum) {
            if (Object.keys(state.skippedKeys).length >= MAX_SKIP) {
                throw new E2EDecryptError('Skip buffer overflow at msgNum=' + msgNum);
            }
            const { msgKey: sk, nextKey } = await advanceChain(new Uint8Array(state.recvChain));
            state.skippedKeys[state.theirRatchetPub + ':' + state.recvMsgNum] = b64uEnc(sk);
            state.recvChain = Array.from(nextKey);
            state.recvMsgNum++;
        }
        const { msgKey: mk, nextKey } = await advanceChain(new Uint8Array(state.recvChain));
        msgKey = mk;
        state.recvChain = Array.from(nextKey);
        state.recvMsgNum++;
    }

    try {
        const plain = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: b64uDec(nonceB64u) },
            await _aesKey(msgKey),
            b64uDec(ciphertextB64u));
        await saveRatchetState(peerId, state);
        return _DEC.decode(plain);
    } catch {
        throw new E2EDecryptError('AES-GCM decryption failed for msgNum=' + msgNum);
    }
}

// ── Test utility (not for production use) ────────────────────────────────────

export function _resetForTesting() {
    _myPrivKey = null;
    _myPrivJwk = null;
    _myPubB64u = null;
    _stateEncKey = null;
    _initDone = false;
    _initPromise = null;
    e2eSupported = false;
    for (const k in _stateCache) delete _stateCache[k];
}
