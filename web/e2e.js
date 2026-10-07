/**
 * web/e2e.js — Double Ratchet E2E encryption for Proxion DMs (Phase 2)
 *
 * Key exchange: X25519 ECDH. Each direction switch rotates the DH ratchet key,
 * re-derives the root key, and starts a new symmetric chain (break-in recovery).
 * State: AES-256-GCM encrypted under a per-device key, kept in localStorage and in
 * a per-device pod copy; the newest copy wins on load (see loadRatchetState).
 *
 * Phase 1 states (no rootKey field) are silently discarded on load.
 */
import { solidSession, podStorageRoot } from './auth.js';
import { isPeerPodRootAllowed } from './ssrf.js';
import { podPublishIdentityAcl } from './pod.js';

export class E2EDecryptError extends Error {
    constructor(msg) { super(msg); this.name = 'E2EDecryptError'; }
}

// Thrown by ratchetDecrypt({ allowInit: false }) when no session exists for the
// peer: the caller has not authenticated the sender, so the message must not
// start one. A subclass, so existing E2EDecryptError handling still applies.
export class E2ENoSessionError extends E2EDecryptError {
    constructor(msg) { super(msg); this.name = 'E2ENoSessionError'; }
}

const _ENC = new TextEncoder();
const _DEC = new TextDecoder();
// Most message keys one incoming message may make us derive and buffer, per
// chain step (anti-DoS bound on work per message; msg_num and pn come off the wire).
export const MAX_SKIP = 20;
// Most skipped message keys kept across all chains; the oldest are evicted first.
export const MAX_SKIPPED_KEYS = 100;

export let e2eSupported = false;

let _myPrivKey   = null;  // CryptoKey (X25519, non-extractable, deriveBits)
let _myPubB64u   = null;  // base64url-encoded 32-byte X25519 public key
let _stateEncKey = null;  // CryptoKey AES-256-GCM (for encrypting ratchet state)
let _initDone    = false;
let _initPromise = null;
const _stateCache = {};   // peerId -> state (in-memory, cleared on reset)

// Per-peer serialization. ratchetEncrypt/ratchetDecrypt each load -> mutate ->
// saveRatchetState (a wholesale replace of the cached state); if a send and a
// pod-drain receive for the same peer interleave across awaits, the later save
// reverts the other's advance, corrupting the chain ("already consumed" /
// dropped messages). Run one ratchet op per peer at a time via a promise chain.
const _peerQueues = {};   // peerId -> tail promise of the queue
function _serialize(peerId, fn) {
    const prev = _peerQueues[peerId] || Promise.resolve();
    const run = prev.catch(() => {}).then(fn);   // wait for prev to settle, then run
    _peerQueues[peerId] = run.catch(() => {});    // a failed op must not poison the chain
    return run;                                    // caller gets fn's real result/rejection
}

// ── Key store (R110): non-extractable identity + state key in IndexedDB ───────
// CryptoKeys stored here are structured-cloned and non-extractable, so an XSS
// cannot exfiltrate the X25519 private scalar the way it could from the old
// localStorage JWK (and the state-encryption key derived from it). Falls back to
// the legacy localStorage keypair when IndexedDB is unavailable.
const _KEY_DB = 'proxion-e2e-keys';
const _KEY_STORE = 'keys';
let _keyDbPromise = null;
function _openKeyDb() {
    if (_keyDbPromise) return _keyDbPromise;
    _keyDbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open(_KEY_DB, 1);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(_KEY_STORE)) db.createObjectStore(_KEY_STORE);
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
    }).catch((err) => { _keyDbPromise = null; throw err; });
    return _keyDbPromise;
}
async function _idbGetKeys(recKey) {
    const db = await _openKeyDb();
    return new Promise((resolve, reject) => {
        const r = db.transaction(_KEY_STORE, 'readonly').objectStore(_KEY_STORE).get(recKey);
        r.onsuccess = () => resolve(r.result || null);
        r.onerror = () => reject(r.error);
    });
}
async function _idbPutKeys(rec, recKey) {
    const db = await _openKeyDb();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(_KEY_STORE, 'readwrite');
        tx.objectStore(_KEY_STORE).put(rec, recKey);
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => reject(tx.error);
    });
}

// ── Per-account namespacing (R134) ────────────────────────────────────────────
// The identity key, cached peer keys, ratchet state and verification badges were
// stored under a single global key ('self' / bare localStorage names). Two Solid
// accounts signed in on the same browser therefore shared ONE E2E identity: the
// second account adopted the first's private key and trust marks. Bind all of it
// to the signed-in WebID instead, mirroring accountDbName() in auth.js. When no
// pod account is signed in (gateway/local-only mode) the bare names are kept, so
// that single-identity case is unchanged.
//
// Migration is deliberately non-destructive: the FIRST account to sign in claims
// the pre-existing legacy identity (copying it into its namespace and recording a
// one-account marker), and the legacy record is NEVER deleted. A regenerated
// identity key is irreversible — it changes the user's public key and voids every
// peer's verification — so the legacy key can only ever be claimed, not replaced.
const _LEGACY_ID = 'self';
const _LEGACY_CLAIM_KEY = 'proxion_e2e_legacy_claimed';   // WebID that claimed legacy 'self'
// Peer-scoped localStorage families that must not bleed across accounts.
const _LS_PEER_PREFIXES = [
    'proxion_e2e_state_', 'proxion_e2e_peer_pub_', 'proxion_e2e_verified_', 'proxion_verified_',
];

function _currentWebId() {
    return (solidSession?.info?.isLoggedIn && solidSession.info.webId) || null;
}

// IndexedDB record key for the identity: account-scoped when signed in, else 'self'.
function _selfKeyId() {
    const webId = _currentWebId();
    return webId ? _LEGACY_ID + '::' + webId : _LEGACY_ID;
}

// Account-scope a localStorage key to the signed-in WebID (mirrors accountDbName).
// Exported so the UI modules that read/write these keys stay consistent.
export function e2eScopedKey(key) {
    const webId = _currentWebId();
    return webId ? key + '::' + webId : key;
}

function _legacyClaimant() {
    try { return localStorage.getItem(_LEGACY_CLAIM_KEY); } catch { return null; }
}
function _setLegacyClaimant(webId) {
    try { localStorage.setItem(_LEGACY_CLAIM_KEY, webId); } catch { /* ignore */ }
}

// Move any un-namespaced legacy peer-cache entries into this account's namespace
// and drop the legacy originals, so a second account signed in later cannot read
// the first account's cached peer keys or verification marks. Only pure-legacy
// keys (no '::' account suffix) are touched; runs once, under the legacy claim.
function _migrateLegacyLsCaches(webId) {
    try {
        const move = [];
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (!k || k.includes('::')) continue;
            if (_LS_PEER_PREFIXES.some((p) => k.startsWith(p))) move.push(k);
        }
        for (const k of move) {
            const v = localStorage.getItem(k);
            if (v !== null) localStorage.setItem(k + '::' + webId, v);
            localStorage.removeItem(k);
        }
    } catch { /* ignore */ }
}

// Generate a fresh non-extractable identity + state key record.
async function _genIdentity() {
    const kp = await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
    const pubJwk = await crypto.subtle.exportKey('jwk', kp.publicKey);   // public export is allowed
    const stateKey = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    return { x25519Priv: kp.privateKey, x25519Pub: pubJwk.x, stateKey };
}

// Load the ancient pre-IndexedDB localStorage JWK identity (R110 and earlier), if
// present, deriving the state key at its EXISTING value so stored state decrypts.
// Returns a record or null.
async function _identityFromLsJwk() {
    const savedJwk = localStorage.getItem('proxion_e2e_x25519_priv_jwk');
    const savedPub = localStorage.getItem('proxion_e2e_x25519_pub_b64u');
    if (!savedJwk || !savedPub) return null;
    try {
        const jwk = JSON.parse(savedJwk);
        const priv = await crypto.subtle.importKey('jwk', jwk, { name: 'X25519' }, false, ['deriveBits']);
        const raw = await hkdf(b64uDec(jwk.d), 'proxion-e2e-state-v1', '', 256);
        const stateKey = await crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
        return { x25519Priv: priv, x25519Pub: savedPub, stateKey };
    } catch { return null; }
}

// The pre-existing legacy identity (IndexedDB 'self' first, then the ancient
// localStorage JWK), for the first account to claim. Never mutates the source.
async function _loadLegacyIdentity() {
    const rec = await _idbGetKeys(_LEGACY_ID);
    if (rec && rec.x25519Priv && rec.stateKey && rec.x25519Pub) return rec;
    return _identityFromLsJwk();
}

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

    // Preferred path: keep the identity key and state key as non-extractable
    // CryptoKeys in IndexedDB, under a per-account record key (see _selfKeyId).
    // A returning account loads its own record; the FIRST account to sign in on
    // this browser claims the pre-existing legacy identity (non-destructively);
    // any later account gets a fresh key of its own. Fall back to the legacy
    // localStorage keypair only when IndexedDB is unavailable.
    let usedIdb = false;
    try {
        const webId = _currentWebId();
        const keyId = _selfKeyId();
        const rec = await _idbGetKeys(keyId);
        if (rec && rec.x25519Priv && rec.stateKey && rec.x25519Pub) {
            _myPrivKey = rec.x25519Priv;
            _myPubB64u = rec.x25519Pub;
            _stateEncKey = rec.stateKey;
            usedIdb = true;
        } else {
            // No record for this account yet. When signed in, try to claim the
            // legacy identity — but only if unclaimed or already ours, so a second
            // account never adopts the first's key. Legacy is copied, never moved.
            let id = null;
            if (webId) {
                const claimant = _legacyClaimant();
                if (!claimant || claimant === webId) {
                    const legacy = await _loadLegacyIdentity();
                    if (legacy) {
                        id = { x25519Priv: legacy.x25519Priv, x25519Pub: legacy.x25519Pub, stateKey: legacy.stateKey };
                        await _idbPutKeys(id, keyId);
                        _setLegacyClaimant(webId);
                        _migrateLegacyLsCaches(webId);
                    }
                }
            }
            if (!id) {
                // Not-signed-in path also runs the ancient localStorage-JWK
                // migration (single local identity, unchanged from R110); a fresh
                // account with no legacy simply generates a new key.
                if (keyId === _LEGACY_ID) id = await _identityFromLsJwk();
                if (!id) id = await _genIdentity();
                await _idbPutKeys(id, keyId);
                // Only the single-identity ('self') path removes the extractable
                // scalar from localStorage; the account paths leave the legacy
                // record intact (never destructive to a claimable identity).
                if (keyId === _LEGACY_ID) {
                    try { localStorage.removeItem('proxion_e2e_x25519_priv_jwk'); } catch { /* ignore */ }
                    try { localStorage.setItem('proxion_e2e_x25519_pub_b64u', id.x25519Pub); } catch { /* ignore */ }
                }
            }
            _myPrivKey = id.x25519Priv; _myPubB64u = id.x25519Pub; _stateEncKey = id.stateKey;
            usedIdb = true;
        }
    } catch { usedIdb = false; }

    if (!usedIdb) {
        // Legacy fallback (no IndexedDB): keep the extractable JWK in localStorage
        // so the key persists at all. Less private, but preserves functionality.
        const savedJwk = localStorage.getItem('proxion_e2e_x25519_priv_jwk');
        const savedPub = localStorage.getItem('proxion_e2e_x25519_pub_b64u');
        let jwk = null;
        if (savedJwk && savedPub) { try { jwk = JSON.parse(savedJwk); _myPubB64u = savedPub; } catch { jwk = null; } }
        if (!jwk) {
            const kp = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
            jwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
            const pubJwk = await crypto.subtle.exportKey('jwk', kp.publicKey);
            _myPubB64u = pubJwk.x;
            localStorage.setItem('proxion_e2e_x25519_priv_jwk', JSON.stringify(jwk));
            localStorage.setItem('proxion_e2e_x25519_pub_b64u', _myPubB64u);
        }
        _myPrivKey = await crypto.subtle.importKey('jwk', jwk, { name: 'X25519' }, false, ['deriveBits']);
        const raw = await hkdf(b64uDec(jwk.d), 'proxion-e2e-state-v1', '', 256);
        _stateEncKey = await _aesKey(raw);
    }

    _initDone = true;
    e2eSupported = true;

    _publishPubToPod().catch(() => {});
}

async function _publishPubToPod() {
    const root = typeof podStorageRoot === 'function' ? podStorageRoot() : null;
    if (!root || !solidSession?.info?.isLoggedIn) return;
    const url = root + 'proxion/identity/x25519-pub.json';
    const res = await solidSession.fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: 1, pub: _myPubB64u }),
    });
    if (!res || !res.ok) return;
    // A peer must READ this public X25519 key from our pod to discover it without a
    // gateway; give it a public-read ACL so it does not inherit proxion/'s owner-only
    // default and 403 the peer. Public data only (the X25519 public key).
    await podPublishIdentityAcl(url);
}

// ── Peer key management ───────────────────────────────────────────────────────

export function myX25519PubB64u() { return _myPubB64u; }

export function cachePeerPub(peerId, pubB64u) {
    if (!peerId || !pubB64u || typeof pubB64u !== 'string') return;
    const prev = localStorage.getItem(e2eScopedKey('proxion_e2e_peer_pub_' + peerId));
    if (prev && prev !== pubB64u) {
        // The peer's key changed. Any prior "verified" mark applied to the OLD key
        // (its safety number), so it must not carry over to the new one, or the
        // verified badge would lie. Clear it; the user re-verifies the new key.
        try { localStorage.removeItem(e2eScopedKey('proxion_e2e_verified_' + peerId)); } catch { /* ignore */ }
        console.warn('[e2e] peer key changed for', peerId, '- verification cleared');
    }
    localStorage.setItem(e2eScopedKey('proxion_e2e_peer_pub_' + peerId), pubB64u);
    if (prev !== pubB64u) _peerPubListeners.forEach(fn => { try { fn(peerId); } catch { /* ignore */ } });
}

// Notified with the peer id whenever a peer's key is first learned or changes,
// so an open DM can refresh its encryption badge without being reopened.
const _peerPubListeners = new Set();
export function onPeerPubChanged(fn) { _peerPubListeners.add(fn); }

function _peerPub(peerId) {
    return localStorage.getItem(e2eScopedKey('proxion_e2e_peer_pub_' + peerId)) || null;
}

export function isE2EEnabled(peerId) {
    return e2eSupported && !!_peerPub(peerId);
}

// One verification per contact, over the keys that actually encrypt the DM
// (the safety number from safetyNumber()). It records the contact's key that
// was compared, so if their key changes they read unverified again. A legacy
// "1" was cleared on every key change (cachePeerPub), so it still holds.
export function isPeerVerified(peerId) {
    const pub = peerId ? _peerPub(peerId) : null;
    if (!pub) return false;
    const v = localStorage.getItem(e2eScopedKey('proxion_e2e_verified_' + peerId));
    return v === pub || v === '1';
}

export function markPeerVerified(peerId) {
    const pub = peerId ? _peerPub(peerId) : null;
    if (!pub) return false;
    localStorage.setItem(e2eScopedKey('proxion_e2e_verified_' + peerId), pub);
    return true;
}

export async function fetchAndCachePeerPub(peerId, peerPodRoot) {
    if (!peerPodRoot || !solidSession?.info?.isLoggedIn) return false;
    if (!isPeerPodRootAllowed(peerPodRoot, podStorageRoot())) return false;   // SSRF gate, see ssrf.js
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
//   ver:              number          monotonic save counter; the newest copy wins on load
//
// Phase 1 states (no rootKey) are discarded on load → clean re-init.
//
// Storage: localStorage (written synchronously) plus a pod copy at a per-device
// path, proxion/e2e/<peer>/state-<device>.enc, where <device> is this device's
// X25519 public key: the blob is encrypted under this device's state key, so no
// other device can use it and two devices of one account must not share a path.
// The pre-device path proxion/e2e/<peer>/state.enc is read only as a migration
// source when the per-device copy is missing.

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

function _statePodUrls(peerId) {
    const root = typeof podStorageRoot === 'function' ? podStorageRoot() : null;
    if (!root || !solidSession?.info?.isLoggedIn || !_myPubB64u) return null;
    const dir = root + 'proxion/e2e/' + encodeURIComponent(peerId) + '/';
    return { device: dir + 'state-' + _myPubB64u + '.enc', legacy: dir + 'state.enc' };
}

// GET + decrypt a pod state copy. null when missing, unreadable, or written by a
// different device (its state key differs, so the GCM tag fails here).
async function _readPodState(url) {
    try {
        const res = await solidSession.fetch(url);
        if (!res || !res.ok) return null;
        return await _decState(await res.text());
    } catch { return null; }
}

// One in-flight PUT per peer; overlapping saves coalesce to the latest blob so an
// older write can never land after a newer one.
const _podWrites = {};   // peerId -> { pending, running }
function _queuePodWrite(peerId, url, enc) {
    const q = _podWrites[peerId] || (_podWrites[peerId] = { pending: null, running: null });
    q.pending = { url, enc };
    if (q.running) return;
    q.running = (async () => {
        while (q.pending) {
            const job = q.pending;
            q.pending = null;
            try {
                await solidSession.fetch(job.url, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: job.enc,
                });
            } catch { /* best effort; localStorage holds the state */ }
        }
        q.running = null;
    })();
}

// Test hook: resolves once every queued pod state write has been attempted.
export async function _podStateWritesSettled() {
    for (;;) {
        const running = Object.values(_podWrites).map((q) => q.running).filter(Boolean);
        if (!running.length) return;
        await Promise.all(running);
    }
}

export async function loadRatchetState(peerId) {
    if (_stateCache[peerId]) {
        const s = _stateCache[peerId];
        if (!s.rootKey) { delete _stateCache[peerId]; return null; } // Phase 1 migration
        return s;
    }

    // Gather every copy this device can decrypt and keep the newest, so a pod copy
    // whose PUT was lost or is still in flight never rolls the session back (that
    // would reuse message numbers and keys). On a tie localStorage wins.
    let best = null;
    const ls = localStorage.getItem(e2eScopedKey('proxion_e2e_state_' + peerId));
    if (ls) {
        try { best = await _decState(ls); } catch { best = null; }
    }

    const urls = _statePodUrls(peerId);
    let podHasDevice = false;
    if (urls) {
        let pod = await _readPodState(urls.device);
        podHasDevice = !!pod;
        if (!pod) pod = await _readPodState(urls.legacy);   // pre-device layout
        if (pod && (!best || (pod.ver || 0) > (best.ver || 0))) best = pod;
    }

    if (!best || !best.rootKey) return null; // none, or Phase 1 migration
    _stateCache[peerId] = best;
    // Migrate: the per-device copy is missing (new device layout), write it now.
    if (urls && !podHasDevice && _stateEncKey) {
        try { _queuePodWrite(peerId, urls.device, await _encState(best)); } catch { /* ignore */ }
    }
    return best;
}

export async function saveRatchetState(peerId, state) {
    state.ver = (Number.isSafeInteger(state.ver) ? state.ver : 0) + 1;
    _stateCache[peerId] = state;
    if (!_stateEncKey) return;
    const enc = await _encState(state);
    localStorage.setItem(e2eScopedKey('proxion_e2e_state_' + peerId), enc);

    const urls = _statePodUrls(peerId);
    if (urls) _queuePodWrite(peerId, urls.device, enc);
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
    // Advance current recv chain to pn, buffering skipped keys. `pn` arrives off
    // the wire (attacker-controlled), so bound the work with MAX_SKIP exactly as
    // the in-chain loop does — otherwise a forged huge pn spins this in an
    // unbounded HMAC loop and exhausts memory (a classic Double Ratchet DoS).
    // recvChain can be null when we have only ever sent (no recv chain yet);
    // there is nothing to buffer in that case.
    //
    // Keys of chains older than the one being retired are dropped here: after this
    // step only the retiring chain and the new one can still have late messages
    // worth waiting for. At most MAX_SKIP keys of the retiring chain are derived;
    // if more of its messages are missing, the rest are given up rather than
    // failing every message of the new chain (whose header repeats the same pn).
    const retiring = state.theirRatchetPub;
    for (const k of Object.keys(state.skippedKeys)) {
        if (!k.startsWith(retiring + ':')) delete state.skippedKeys[k];
    }
    let recvChain = state.recvChain ? new Uint8Array(state.recvChain) : null;
    const stop = Math.min(pn, state.recvMsgNum + MAX_SKIP);
    while (recvChain && state.recvMsgNum < stop) {
        const { msgKey: sk, nextKey } = await advanceChain(recvChain);
        state.skippedKeys[state.theirRatchetPub + ':' + state.recvMsgNum] = b64uEnc(sk);
        recvChain = nextKey;
        state.recvMsgNum++;
    }
    _capSkippedKeys(state);

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

// Keep at most MAX_SKIPPED_KEYS buffered keys, evicting the oldest. Object key
// order is insertion order for these non-numeric keys, and survives JSON storage.
function _capSkippedKeys(state) {
    const keys = Object.keys(state.skippedKeys);
    for (let i = 0; i < keys.length - MAX_SKIPPED_KEYS; i++) delete state.skippedKeys[keys[i]];
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

export function ratchetEncrypt(peerId, plaintext) {
    return _serialize(peerId, () => _ratchetEncryptImpl(peerId, plaintext));
}
async function _ratchetEncryptImpl(peerId, plaintext) {
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

// opts.allowInit (default true): when false and no session exists for peerId,
// throw E2ENoSessionError instead of creating a responder session from the
// message's ratchet_pub. Callers that have not authenticated the sender pass
// false, so an unauthenticated message can never establish the peer's session.
export function ratchetDecrypt(peerId, ciphertextB64u, nonceB64u, msgNum, ratchetPubB64u, pn = 0, opts = {}) {
    return _serialize(peerId, () => _ratchetDecryptImpl(peerId, ciphertextB64u, nonceB64u, msgNum, ratchetPubB64u, pn, opts));
}

// True when a ratchet session exists for peerId (serialized with ratchet ops).
export function hasRatchetSession(peerId) {
    return _serialize(peerId, async () => !!(await loadRatchetState(peerId)));
}
async function _ratchetDecryptImpl(peerId, ciphertextB64u, nonceB64u, msgNum, ratchetPubB64u, pn = 0, opts = {}) {
    if (!_initDone) await initE2E();
    if (!e2eSupported) throw new E2EDecryptError('E2E not supported');
    if (!ratchetPubB64u) throw new E2EDecryptError('ratchet_pub required');
    msgNum = Number(msgNum);
    pn = Number(pn ?? 0);
    if (!Number.isSafeInteger(msgNum) || msgNum < 0 || !Number.isSafeInteger(pn) || pn < 0) {
        throw new E2EDecryptError('invalid msg_num or pn');
    }

    let state = await loadRatchetState(peerId);

    // Work on a COPY and commit only after the GCM tag verifies (saveRatchetState
    // below). loadRatchetState hands back the live in-memory cached object by
    // reference; mutating it before the message is proven authentic lets a single
    // forged/corrupt ciphertext (valid ratchet_pub header, bad tag) advance — or
    // DH-ratchet — the live session and brick every subsequent real message until
    // reload. Signal commits post-decrypt for exactly this reason.
    if (state) state = JSON.parse(JSON.stringify(state));

    const skipKey = ratchetPubB64u + ':' + msgNum;
    let msgKey;

    // A buffered key is tried before any ratchet step: a late message from an
    // older chain must not DH-ratchet the session back onto that chain's key.
    if (state && Object.prototype.hasOwnProperty.call(state.skippedKeys, skipKey)) {
        msgKey = b64uDec(state.skippedKeys[skipKey]);
        delete state.skippedKeys[skipKey];
    } else {
        if (!state) {
            if (opts && opts.allowInit === false) throw new E2ENoSessionError('No session for peer');
            state = await _initReceiverState(ratchetPubB64u);
        } else if (ratchetPubB64u !== state.theirRatchetPub) {
            state = await _dhRatchetReceive(state, ratchetPubB64u, pn);
        }
        if (msgNum < state.recvMsgNum) {
            throw new E2EDecryptError('Message already consumed: msgNum=' + msgNum);
        }
        // Checked before any key is derived: bounded work per message.
        if (msgNum - state.recvMsgNum > MAX_SKIP) {
            throw new E2EDecryptError('Skip buffer overflow at msgNum=' + msgNum);
        }
        if (!state.recvChain) throw new E2EDecryptError('no receiving chain');
        while (state.recvMsgNum < msgNum) {
            const { msgKey: sk, nextKey } = await advanceChain(new Uint8Array(state.recvChain));
            state.skippedKeys[state.theirRatchetPub + ':' + state.recvMsgNum] = b64uEnc(sk);
            state.recvChain = Array.from(nextKey);
            state.recvMsgNum++;
        }
        const { msgKey: mk, nextKey } = await advanceChain(new Uint8Array(state.recvChain));
        msgKey = mk;
        state.recvChain = Array.from(nextKey);
        state.recvMsgNum++;
        _capSkippedKeys(state);
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
    _myPubB64u = null;
    _stateEncKey = null;
    _initDone = false;
    _initPromise = null;
    e2eSupported = false;
    _keyDbPromise = null;
    for (const k in _stateCache) delete _stateCache[k];
    for (const k in _peerQueues) delete _peerQueues[k];
    for (const k in _podWrites) delete _podWrites[k];
}
