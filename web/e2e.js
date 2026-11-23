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
import { solidSession, podStorageRoot, authContext } from './auth.js';
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

// Private runtime state is owned by one account. Session generations cancel
// async work without changing persisted account/device namespaces.
let _runtimeOwner = undefined, _runtimeEpoch = null, _runtimeGeneration = 0;
let _activityCheck = () => true, _wasActive = true;
export function setE2EActivityCheck(current) {
    _activityCheck = current; _runtimeGeneration++; _initPromise = null;
}
export class E2EContextError extends Error {
    constructor() { super('Encryption context changed; retry after signing in'); this.name = 'E2EContextError'; }
}
function _syncRuntime() {
    const context = authContext();
    if (_runtimeOwner !== context.webId) {
        _runtimeOwner = context.webId;
        _myPrivKey = _myPubB64u = _stateEncKey = null;
        _initDone = false; e2eSupported = false;
        for (const key in _stateCache) delete _stateCache[key];
    }
    const active = !!_activityCheck();
    if (_runtimeEpoch !== context.epoch || active !== _wasActive) {
        _wasActive = active;
        _runtimeEpoch = context.epoch;
        _runtimeGeneration++;
        _initPromise = null;
    }
    return context;
}
function _cryptoContext() {
    const auth = _syncRuntime(), generation = _runtimeGeneration;
    const current = () => {
        const next = _syncRuntime();
        return generation === _runtimeGeneration && _activityCheck() && (!next.webId || next.authenticated);
    };
    return { webId: auth.webId, generation, current,
        assert: () => { if (!current()) throw new E2EContextError(); } };
}
async function _ready(context) {
    context.assert();
    await initE2E();
    context.assert();
}

// Per-peer serialization. ratchetEncrypt/ratchetDecrypt each load -> mutate ->
// saveRatchetState (a wholesale replace of the cached state); if a send and a
// pod-drain receive for the same peer interleave across awaits, the later save
// reverts the other's advance, corrupting the chain ("already consumed" /
// dropped messages). Run one ratchet op per peer at a time via a promise chain.
const _peerQueues = {};   // peerId -> tail promise of the queue
function _serialize(peerId, fn) {
    const context = _cryptoContext();
    const key = JSON.stringify([context.webId, context.generation, peerId]);
    const prev = _peerQueues[key] || Promise.resolve();
    const run = prev.catch(() => {}).then(async () => {
        context.assert();
        const result = await fn(context);
        context.assert();
        return result;
    });
    const tail = run.catch(() => {});
    _peerQueues[key] = tail;
    tail.then(() => { if (_peerQueues[key] === tail) delete _peerQueues[key]; });
    return run;
}

// Resolves once every ratchet operation queued so far has finished and saved
// its state, so another tab can take over without loading a stale session.
export async function ratchetIdle() {
    for (;;) {
        const tails = Object.values(_peerQueues);
        await Promise.all(tails);
        if (Object.values(_peerQueues).every((q, i) => q === tails[i]) && Object.keys(_peerQueues).length === tails.length) return;
    }
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
// Atomic insert/legacy claim: concurrent tabs cannot replace an identity or
// let two accounts adopt the same unclaimed legacy key.
async function _idbEnsureKeys(candidate, recKey, legacy, context) {
    const db = await _openKeyDb();
    context.assert();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(_KEY_STORE, 'readwrite'), store = tx.objectStore(_KEY_STORE);
        let result;
        const read = store.get(recKey);
        const claim = store.get('legacy-owner');
        claim.onsuccess = () => {
            if (!context.current()) { tx.abort(); return; }
            result = read.result;
            if (!result) {
                const owner = claim.result || _legacyClaimant();
                const useLegacy = legacy && context.webId && (!owner || owner === context.webId);
                result = useLegacy ? legacy : candidate;
                store.put(result, recKey);
                if (useLegacy) {
                    // Reserve the local fallback claim before this transaction
                    // can finish; a subsequent storage outage must honor it.
                    _setLegacyClaimant(context.webId);
                    store.put(context.webId, 'legacy-owner');
                }
            }
        };
        tx.oncomplete = () => resolve({ record: result, claimant: claim.result || (result === legacy ? context.webId : null) });
        tx.onabort = tx.onerror = () => reject(context.current() ? (tx.error || Error('Key storage failed')) : new E2EContextError());
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
    return _syncRuntime().webId;
}

// IndexedDB record key for the identity: account-scoped when signed in, else 'self'.
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
async function _identityFromLsJwk(suffix = '') {
    const savedJwk = localStorage.getItem('proxion_e2e_x25519_priv_jwk' + suffix);
    const savedPub = localStorage.getItem('proxion_e2e_x25519_pub_b64u' + suffix);
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
    const context = _cryptoContext();
    context.assert();
    if (_initDone) return;
    if (!_initPromise) {
        _initPromise = _doInit(context).catch(err => {
            if (err instanceof E2EContextError) throw err;
            console.warn('[e2e] init failed, E2E disabled:', err);
        });
    }
    return _initPromise;
}

async function _fallbackIdentity(context) {
    const suffix = context.webId ? '::' + context.webId : '';
    const privKey = 'proxion_e2e_x25519_priv_jwk' + suffix;
    const pubKey = 'proxion_e2e_x25519_pub_b64u' + suffix;
    context.assert();
    // Claim legacy synchronously before any crypto await. Other accounts must
    // create their own scoped key, including when IndexedDB is unavailable.
    if (context.webId && !localStorage.getItem(privKey)
            && (!_legacyClaimant() || _legacyClaimant() === context.webId)) {
        const priv = localStorage.getItem('proxion_e2e_x25519_priv_jwk');
        const pub = localStorage.getItem('proxion_e2e_x25519_pub_b64u');
        if (priv && pub) {
            _setLegacyClaimant(context.webId);
            localStorage.setItem(privKey, priv); localStorage.setItem(pubKey, pub);
            _migrateLegacyLsCaches(context.webId);
        }
    }
    let jwk = null, pub = localStorage.getItem(pubKey);
    try { jwk = JSON.parse(localStorage.getItem(privKey)); } catch { /* missing legacy key */ }
    if (!jwk || !pub) {
        const pair = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
        jwk = await crypto.subtle.exportKey('jwk', pair.privateKey);
        pub = (await crypto.subtle.exportKey('jwk', pair.publicKey)).x;
        context.assert();
        // A parallel initializer may already have committed this same identity.
        if (localStorage.getItem(privKey) && localStorage.getItem(pubKey)) {
            jwk = JSON.parse(localStorage.getItem(privKey)); pub = localStorage.getItem(pubKey);
        } else {
            localStorage.setItem(privKey, JSON.stringify(jwk)); localStorage.setItem(pubKey, pub);
        }
    }
    const priv = await crypto.subtle.importKey('jwk', jwk, { name: 'X25519' }, false, ['deriveBits']);
    const raw = await hkdf(b64uDec(jwk.d), 'proxion-e2e-state-v1', '', 256);
    const stateKey = await _aesKey(raw);
    context.assert();
    return { x25519Priv: priv, x25519Pub: pub, stateKey };
}

async function _doInit(context) {
    const keyId = context.webId ? _LEGACY_ID + '::' + context.webId : _LEGACY_ID;
    let identity;
    try {
        identity = await _idbGetKeys(keyId);
        context.assert();
        if (!identity) {
            const legacy = context.webId ? await _loadLegacyIdentity() : await _identityFromLsJwk();
            context.assert();
            // Account-scoped fallback keys must migrate into IndexedDB too when
            // storage becomes available again; never rotate a returning user.
            const scoped = context.webId ? await _identityFromLsJwk('::' + context.webId) : null;
            const candidate = scoped || (!context.webId && legacy) || await _genIdentity();
            context.assert();
            const saved = await _idbEnsureKeys(candidate, keyId, scoped ? null : legacy, context);
            context.assert();
            identity = saved.record;
            if (context.webId && saved.claimant === context.webId) {
                _setLegacyClaimant(context.webId); _migrateLegacyLsCaches(context.webId);
            }
            if (!context.webId) {
                localStorage.removeItem('proxion_e2e_x25519_priv_jwk');
                localStorage.setItem('proxion_e2e_x25519_pub_b64u', identity.x25519Pub);
            }
        }
    } catch (err) {
        context.assert(); // cancellation is never a reason to use legacy keys
        if (err instanceof E2EContextError) throw err;
        identity = await _fallbackIdentity(context);
    }
    context.assert();
    _myPrivKey = identity.x25519Priv; _myPubB64u = identity.x25519Pub; _stateEncKey = identity.stateKey;
    _initDone = true; e2eSupported = true;
    _publishPubToPod(context).catch(() => {});
}

async function _publishPubToPod(context) {
    context.assert();
    const root = typeof podStorageRoot === 'function' ? podStorageRoot() : null;
    if (!root || !solidSession?.info?.isLoggedIn) return;
    const url = root + 'proxion/identity/x25519-pub.json';
    const res = await solidSession.fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: 1, pub: _myPubB64u }),
    });
    if (!context.current() || !res || !res.ok) return;
    // A peer must READ this public X25519 key from our pod to discover it without a
    // gateway; give it a public-read ACL so it does not inherit proxion/'s owner-only
    // default and 403 the peer. Public data only (the X25519 public key).
    await podPublishIdentityAcl(url);
}

// ── Peer key management ───────────────────────────────────────────────────────

export function myX25519PubB64u() { _syncRuntime(); return _myPubB64u; }

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
    _syncRuntime();
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

// via: { fetch, ownRoot, current? } reads through another pod fetch (the desktop client's
// gateway pod bridge, gwpod.js) instead of the Solid session.
export async function fetchAndCachePeerPub(peerId, peerPodRoot, via = null) {
    const cryptoContext = _cryptoContext();
    const session = solidSession;
    const webId = _currentWebId();
    const sessionId = session?.info?.sessionId;
    const doFetch = via ? via.fetch : (u) => session.fetch(u);
    const ownRoot = via ? via.ownRoot : podStorageRoot();
    if (!peerPodRoot || !(via || solidSession?.info?.isLoggedIn)) return false;
    if (!isPeerPodRootAllowed(peerPodRoot, ownRoot)) return false;   // SSRF gate, see ssrf.js
    // Never apply a delayed result to a different account's cache or trust mark.
    const current = () => cryptoContext.current() && solidSession === session && _currentWebId() === webId
        && session?.info?.sessionId === sessionId
        && (via ? (!via.current || via.current())
            : session?.info?.isLoggedIn && podStorageRoot() === ownRoot);
    try {
        if (!current()) return false;
        const res = await doFetch(peerPodRoot + 'proxion/identity/x25519-pub.json');
        if (!current() || !res.ok) return false;
        const data = await res.json();
        if (!current()) return false;
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

// Storage awaits and queued backups must never inherit a replacement account's
// credentials, root or device key. Capture before doing any asynchronous work.
function _stateContext() {
    const cryptoContext = _cryptoContext();
    const session = solidSession;
    const webId = _currentWebId();
    const loggedIn = !!session?.info?.isLoggedIn;
    const sessionId = session?.info?.sessionId;
    const root = podStorageRoot();
    const pub = _myPubB64u, stateKey = _stateEncKey;
    return {
        webId,
        fetch: session?.fetch?.bind(session),
        current: () => cryptoContext.current() && solidSession === session && _currentWebId() === webId
            && !!session?.info?.isLoggedIn === loggedIn && session?.info?.sessionId === sessionId
            && podStorageRoot() === root && _myPubB64u === pub && _stateEncKey === stateKey,
    };
}

// GET + decrypt a pod state copy. null when missing, unreadable, or written by a
// different device (its state key differs, so the GCM tag fails here).
async function _readPodState(url, context) {
    try {
        if (!context.current()) return null;
        const res = await context.fetch(url);
        if (!context.current() || !res || !res.ok) return null;
        const enc = await res.text();
        if (!context.current()) return null;
        const state = await _decState(enc);
        return context.current() ? state : null;
    } catch { return null; }
}

// One in-flight PUT per account/resource; overlapping saves coalesce to the
// latest blob. Another account's resource need not wait on a stalled old PUT.
const _podWrites = {};   // account/resource -> { pending, running }
function _queuePodWrite(peerId, url, enc, context) {
    const key = JSON.stringify([context.webId, peerId, url]);
    const q = _podWrites[key] || (_podWrites[key] = { pending: null, running: null });
    q.pending = { url, enc, context };
    if (q.running) return;
    q.running = Promise.resolve().then(async () => {
        while (q.pending) {
            const job = q.pending;
            q.pending = null;
            if (!job.context.current()) continue;
            try {
                await job.context.fetch(job.url, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: job.enc,
                });
            } catch { /* best effort; localStorage holds the state */ }
        }
        q.running = null;
    });
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
    await _ready(_cryptoContext());
    const context = _stateContext();
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

    if (!context.current()) throw new E2EDecryptError('State context changed');
    const urls = _statePodUrls(peerId);
    let podHasDevice = false;
    if (urls) {
        let pod = await _readPodState(urls.device, context);
        if (!context.current()) throw new E2EDecryptError('State context changed');
        podHasDevice = !!pod;
        if (!pod) pod = await _readPodState(urls.legacy, context);   // pre-device layout
        if (pod && (!best || (pod.ver || 0) > (best.ver || 0))) best = pod;
    }

    if (!context.current()) throw new E2EDecryptError('State context changed');
    if (!best || !best.rootKey) return null; // none, or Phase 1 migration
    // Migrate: the per-device copy is missing (new device layout), write it now.
    if (urls && !podHasDevice && _stateEncKey) {
        try { _queuePodWrite(peerId, urls.device, await _encState(best), context); } catch { /* ignore */ }
    }
    if (!context.current()) throw new E2EDecryptError('State context changed');
    _stateCache[peerId] = best;
    return best;
}

export async function saveRatchetState(peerId, state, { current = () => true } = {}) {
    if (!current()) throw new E2EContextError();
    await _ready(_cryptoContext());
    const context = _stateContext();
    const storageKey = e2eScopedKey('proxion_e2e_state_' + peerId);
    const next = { ...state, ver: (Number.isSafeInteger(state.ver) ? state.ver : 0) + 1 };
    const urls = _statePodUrls(peerId);
    if (!current() || !context.current()) throw new E2EDecryptError('State context changed');
    if (_stateEncKey) {
        const enc = await _encState(next);
        if (!current() || !context.current() || storageKey !== e2eScopedKey('proxion_e2e_state_' + peerId)) {
            throw new E2EDecryptError('Receive context changed');
        }
        // The encrypted recovery copy and spent key advance share this atomic write.
        // A failed write must leave the live session retryable too.
        localStorage.setItem(storageKey, enc);
        _stateCache[peerId] = next;
        if (urls) _queuePodWrite(peerId, urls.device, enc, context);
    } else {
        _stateCache[peerId] = next;
    }
}

// Recovery entries contain plaintext only inside the existing encrypted state.
// Remove one only after history and a durable exact-envelope receipt were saved.
export function finishDmReceive(peerId, receiveId, current = () => true) {
    return _serialize(peerId, async context => {
        const callerCurrent = current;
        current = () => context.current() && callerCurrent();
        if (!current()) return false;
        const state = await loadRatchetState(peerId);
        if (!current()) return false;
        if (state?.pendingReceives?.[receiveId] === undefined) return true;
        const next = { ...state, pendingReceives: { ...state.pendingReceives } };
        delete next.pendingReceives[receiveId];
        if (state.pendingReceiveData) {
            next.pendingReceiveData = { ...state.pendingReceiveData };
            delete next.pendingReceiveData[receiveId];
        }
        await saveRatchetState(peerId, next, { current });
        return true;
    });
}

// Local recovery needs message metadata when a gateway will not replay a socket
// event after reload. The metadata is encrypted in the same state record.
export async function pendingDmReceives({ transport = 'gateway' } = {}) {
    if (!['gateway', 'native-media', 'native-edit'].includes(transport)) throw new TypeError('Unsupported receive transport');
    await _ready(_cryptoContext());
    const context = _stateContext(), webId = _currentWebId();
    const prefix = 'proxion_e2e_state_', suffix = webId ? '::' + webId : '';
    const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i));
    const rows = [];
    for (const key of keys) {
        if (!context.current()) throw new E2EDecryptError('State context changed');
        if (!key?.startsWith(prefix) || (suffix ? !key.endsWith(suffix) : key.includes('::'))) continue;
        const peerId = key.slice(prefix.length, suffix ? -suffix.length : undefined);
        let state;
        try { state = await _decState(localStorage.getItem(key)); } catch { continue; }
        if (!context.current()) throw new E2EDecryptError('State context changed');
        if (!state?.rootKey) continue;
        for (const [receiveId, data] of Object.entries(state.pendingReceiveData || {})) {
            if (/^[a-f0-9]{64}$/.test(receiveId) && data?.transport === transport
                    && typeof state.pendingReceives?.[receiveId] === 'string') {
                rows.push({ peerId, receiveId, content: state.pendingReceives[receiveId], data });
            }
        }
    }
    return rows;
}

// Cancellation is safe only before any target has spent a send key. This
// serialized read also recognizes preparation interrupted before job.frames.
export function hasPendingDmSend(peerId, sendId) {
    return _serialize(peerId, async () => !!(await loadRatchetState(peerId))?.pendingSends?.[sendId]);
}
export function cachedPeerPub(peerId) { return _peerPub(peerId); }

// A native file header may be waiting in a different transport from text on
// this same chain. Hold later encryption until that exact header is durably
// consumed. Custody alone must never call this release operation.
export class E2EHeaderPendingError extends Error {
    constructor(message = 'An earlier file header is awaiting durable receive') {
        super(message); this.name = 'E2EHeaderPendingError';
    }
}
export function dmSendHoldState(peerId, sendId) {
    return _serialize(peerId, async () => (await loadRatchetState(peerId))?.sendHolds?.[sendId] || null);
}
export function releaseDmSendHold(peerId, sendId, current = () => true) {
    return _serialize(peerId, async context => {
        const active = () => context.current() && current();
        if (!active()) return false;
        const state = await loadRatchetState(peerId);
        if (!active()) return false;
        if (state?.sendHolds?.[sendId] === 'received') return true;
        if (state?.sendHolds?.[sendId] !== 'waiting' || state.sendBarrier !== sendId
                || !state.pendingSends?.[sendId]) return false;
        const next = { ...state, sendHolds: { ...state.sendHolds, [sendId]: 'received' } };
        delete next.sendBarrier;
        await saveRatchetState(peerId, next, { current: active });
        return true;
    });
}

export function finishDmSend(peerId, sendId, current = () => true) {
    return _serialize(peerId, async context => {
        const callerCurrent = current;
        current = () => context.current() && callerCurrent();
        if (!current()) return false;
        const state = await loadRatchetState(peerId);
        if (!current()) return false;
        if (state?.sendBarrier === sendId || state?.sendHolds?.[sendId] === 'waiting') return false;
        if (!state?.pendingSends?.[sendId]) return true;
        const next = { ...state, pendingSends: { ...state.pendingSends }, sendHolds: { ...state.sendHolds } };
        delete next.sendHolds[sendId];
        delete next.pendingSends[sendId];
        await saveRatchetState(peerId, next, { current });
        return true;
    });
}

const _SEND_JOB_PREFIX = 'proxion_e2e_send_job_';
function _sendJobKey(id) {
    if (typeof id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id)) throw Error('Invalid send job id');
    return e2eScopedKey(_SEND_JOB_PREFIX + id);
}
export async function saveDmSendJob(job, current = () => true) {
    await _ready(_cryptoContext());
    if (!_stateEncKey) throw Error('Encrypted send storage unavailable');
    const context = _stateContext(), key = _sendJobKey(job.id);
    const enc = await _encState({ job });
    if (!current() || !context.current() || key !== _sendJobKey(job.id)) throw Error('Send context changed');
    localStorage.setItem(key, enc);
}
export async function listDmSendJobs() {
    await _ready(_cryptoContext());
    const context = _stateContext(), webId = _currentWebId(), suffix = webId ? '::' + webId : '';
    const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i));
    const jobs = [];
    for (const key of keys) {
        if (!context.current()) throw Error('Send context changed');
        if (!key?.startsWith(_SEND_JOB_PREFIX) || (suffix ? !key.endsWith(suffix) : key.includes('::'))) continue;
        let row;
        try { row = await _decState(localStorage.getItem(key)); } catch { continue; }
        if (!context.current()) throw Error('Send context changed');
        if (row?.job && key === _sendJobKey(row.job.id)) jobs.push(row.job);
    }
    return jobs;
}
export function removeDmSendJob(id, current = () => true) {
    if (!current() || !_cryptoContext().current()) return false;
    localStorage.removeItem(_sendJobKey(id));
    return true;
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

async function _initReceiverState(senderRatchetPubB64u, identityKey) {
    const senderRatchetKey = await crypto.subtle.importKey(
        'raw', b64uDec(senderRatchetPubB64u), { name: 'X25519' }, false, []);

    // Derive initial recv chain (symmetric with sender's send chain)
    const dhBits = new Uint8Array(await crypto.subtle.deriveBits(
        { name: 'X25519', public: senderRatchetKey }, identityKey, 256));
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

export function ratchetEncrypt(peerId, plaintext, opts = {}) {
    return _serialize(peerId, context => _ratchetEncryptImpl(peerId, plaintext,
        { ...opts, current: () => context.current() && (!opts.current || opts.current()) }));
}
async function _ratchetEncryptImpl(peerId, plaintext, opts = {}) {
    await _ready(_cryptoContext());
    if (!e2eSupported) throw new Error('E2E not supported in this browser');

    const current = opts.current || (() => true), sendId = opts.sendId;
    if (sendId !== undefined && (typeof sendId !== 'string' || !/^[a-f0-9]{64}$/.test(sendId))) throw Error('Invalid send id');
    if (opts.holdForReceive && !sendId) throw Error('A receive hold requires a stable send id');
    if (!current()) throw Error('Send context changed');
    let state = await loadRatchetState(peerId);
    if (!current()) throw Error('Send context changed');
    if (sendId && state?.pendingSends?.[sendId]) {
        // Old preparations without a hold cannot silently acquire one at an
        // already-spent position. A previously released hold replays unchanged.
        if (opts.holdForReceive && !state.sendHolds?.[sendId]) {
            throw new E2EHeaderPendingError('Prepared header lacks original receive-hold evidence');
        }
        return { ...state.pendingSends[sendId] };
    }
    if (state?.sendBarrier) throw new E2EHeaderPendingError();
    if (sendId && Object.keys(state?.pendingSends || {}).length >= 64) throw Error('Pending send recovery is full');
    // A failed commit must not advance the live sender session either.
    if (state) state = JSON.parse(JSON.stringify(state));

    if (!state) {
        const peerPubB64u = opts.peerPub || _peerPub(peerId);
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

    const result = { ciphertext: b64uEnc(ct), nonce: b64uEnc(nonce), msgNum, pn, ratchetPub };
    if (sendId) state.pendingSends = { ...state.pendingSends, [sendId]: result };
    if (opts.holdForReceive) {
        state.sendHolds = { ...state.sendHolds, [sendId]: 'waiting' };
        state.sendBarrier = sendId;
    }
    await saveRatchetState(peerId, state, { current });
    return result;
}

// ── ratchetDecrypt ────────────────────────────────────────────────────────────

// opts.allowInit (default true): when false and no session exists for peerId,
// throw E2ENoSessionError instead of creating a responder session from the
// message's ratchet_pub. Callers that have not authenticated the sender pass
// false, so an unauthenticated message can never establish the peer's session.
export function ratchetDecrypt(peerId, ciphertextB64u, nonceB64u, msgNum, ratchetPubB64u, pn = 0, opts = {}) {
    return _serialize(peerId, context => _ratchetDecryptImpl(peerId, ciphertextB64u, nonceB64u, msgNum, ratchetPubB64u, pn,
        { ...opts, current: () => context.current() && (!opts.current || opts.current()) }));
}

// True when a ratchet session exists for peerId (serialized with ratchet ops).
export function hasRatchetSession(peerId) {
    return _serialize(peerId, async () => !!(await loadRatchetState(peerId)));
}
async function _ratchetDecryptImpl(peerId, ciphertextB64u, nonceB64u, msgNum, ratchetPubB64u, pn = 0, opts = {}) {
    await _ready(_cryptoContext());
    if (!e2eSupported) throw new E2EDecryptError('E2E not supported');
    if (!ratchetPubB64u) throw new E2EDecryptError('ratchet_pub required');
    msgNum = Number(msgNum);
    pn = Number(pn ?? 0);
    if (!Number.isSafeInteger(msgNum) || msgNum < 0 || !Number.isSafeInteger(pn) || pn < 0) {
        throw new E2EDecryptError('invalid msg_num or pn');
    }

    const current = opts.current || (() => true);
    const identityKey = _myPrivKey;
    if (!current()) throw new E2EDecryptError('Receive context changed');
    const receiveId = opts.receiveId;
    if (receiveId !== undefined && (typeof receiveId !== 'string' || !/^[a-f0-9]{64}$/.test(receiveId))) {
        throw new E2EDecryptError('Invalid receive fingerprint');
    }
    let state = await loadRatchetState(peerId);
    if (!current()) throw new E2EDecryptError('Receive context changed');
    if (receiveId && state?.pendingReceives?.[receiveId] !== undefined) {
        return state.pendingReceives[receiveId];
    }
    if (receiveId && Object.keys(state?.pendingReceives || {}).length >= 64) {
        throw new E2EDecryptError('Pending receive recovery is full');
    }

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
            state = await _initReceiverState(ratchetPubB64u, identityKey);
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

    let plain;
    try {
        plain = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: b64uDec(nonceB64u) },
            await _aesKey(msgKey),
            b64uDec(ciphertextB64u));
    } catch {
        throw new E2EDecryptError('AES-GCM decryption failed for msgNum=' + msgNum);
    }
    const text = _DEC.decode(plain);
    if (receiveId) {
        state.pendingReceives = { ...state.pendingReceives, [receiveId]: text };
        if (opts.receiveData) state.pendingReceiveData = { ...state.pendingReceiveData, [receiveId]: opts.receiveData };
    }
    // Storage failure is retryable; do not mislabel it as invalid ciphertext.
    await saveRatchetState(peerId, state, { current });
    return text;
}

// ── Test utility (not for production use) ────────────────────────────────────

export function _resetForTesting() {
    _runtimeOwner = undefined; _runtimeEpoch = null; _runtimeGeneration++;
    _activityCheck = () => true; _wasActive = true;
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
