// webdm.js — gateway-free direct messages (R103).
//
// Delivery and receipt for DMs when there is no gateway. Sending: main.js has
// already ratchet-encrypted the payload (e2e.js), so this just wraps it in an
// envelope and drops it into the recipient's pod (pod.js podDropDm). Receiving:
// watch our own DM drop box (notify.js), and for each envelope decrypt it with
// the ratchet and hand main.js a normal "message" event, then delete the drop.
//
// The pod only ever stores ciphertext. Dependencies are injected so this is
// unit-testable without a browser or a live pod.

// The recipient's pod storage root, derived from their WebID. CSS-style WebIDs
// live at {root}profile/card#me; fall back to the origin.
export function peerPodRootFromWebId(webId) {
    if (!webId) return null;
    const noFrag = webId.split('#')[0];
    const i = noFrag.indexOf('/profile/');
    if (i > 0) return noFrag.slice(0, i + 1);
    try { return new URL(webId).origin + '/'; } catch { return null; }
}

// A WebID a web DM can be addressed to: https, or http only on a loopback host
// (a local test pod). Also the shape DM thread ids take in the web build.
// It must look like a profile: a #fragment or a profile/card path, and no
// query. A gateway link (/i/<token>, ?from=, ?join=, a bare site) is not one.
const _LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]']);
export function isDmPeerWebId(id) {
    if (typeof id !== 'string' || id.length > 2048) return false;
    if (/[<>"{}|\\^`\s]/.test(id)) return false;
    let u;
    try { u = new URL(id); } catch { return false; }
    if (u.search || /^\/i\/[^/]*\/?$/.test(u.pathname)) return false;
    if (!(u.hash.length > 1 || /\/(profile|card)(\/|$)/i.test(u.pathname))) return false;
    if (u.protocol === 'https:') return true;
    return u.protocol === 'http:' && _LOOPBACK.has(u.hostname);
}

// Build the wire envelope from an (already-encrypted) DM command payload.
export function envelopeFromDmPayload(payload, selfWebId, displayName = '') {
    return {
        v: 1,
        from_webid: selfWebId,
        from_display_name: displayName || '',
        message_id: payload.message_id,
        content: payload.content,                 // ciphertext when e2e
        e2e: !!payload.e2e,
        nonce: payload.nonce || null,
        msg_num: payload.msg_num ?? null,
        pn: payload.pn ?? null,
        ratchet_pub: payload.ratchet_pub || null,
        x25519_pub: payload.x25519_pub || null,
        reply_to_id: payload.reply_to_id || null,
        timestamp: new Date().toISOString(),
    };
}

// Sender verification result for a received envelope:
//   'verified'    the signature verifies and the sender's own pod names its signer
//   'unavailable' the signature verifies but the sender's signer identity could
//                 not be fetched this time (retry later)
//   'rejected'    unsigned, bad signature, or a signer the sender did not publish
// A boolean from an injected verifier maps true -> verified, false -> rejected.
function _verifyStatus(v) {
    if (v === true || v === 'verified') return 'verified';
    if (v === 'unavailable') return 'unavailable';
    return 'rejected';
}

// Build the verifySender used by createWebDm. Dependencies are injected so it is
// testable without a pod: verifyDmSig/verifyFanoutSig (dmsig.js), fetchSigner
// (pod.js podFetchPeerSigner, null when unreachable), verifyDeviceCert
// (device-cert.js).
export function createSenderVerifier({ verifyDmSig, verifyFanoutSig, fetchSigner, verifyDeviceCert }) {
    return async function verifySender(env) {
        if (!env || !env.from_webid || !env.signer || !env.sig) return 'rejected';
        const sigOk = env.kind === 'fanout' ? await verifyFanoutSig(env) : await verifyDmSig(env);
        if (!sigOk) return 'rejected';
        const iddoc = await fetchSigner(peerPodRootFromWebId(env.from_webid));
        if (!iddoc) return 'unavailable';
        if (env.signer === iddoc.signer) return 'verified';
        if (iddoc.account_did && env.cert && verifyDeviceCert) {
            const acct = await verifyDeviceCert(env.cert, { expectedAccountDid: iddoc.account_did, expectedDeviceDid: env.signer });
            if (acct && acct === iddoc.account_did) return 'verified';
        }
        return 'rejected';
    };
}

// Optional, for the desktop client (gwpod.js): isKnownSender(webid) and
// onMessageRequest(info) hold a verified drop from someone who is not a contact
// as a message request (left in the inbox, not decrypted) until the user
// accepts; strictSender also refuses an unverified unencrypted drop instead of
// showing it. The browser build passes none of these and behaves as before.
export function createWebDm({ pod, e2e, notify, handleEvent, getSelfWebId, getDisplayName, getMyDeviceId, persistMessage, signEnvelope, verifySender, onUnverifiedDrop, isBlocked, isKnownSender, onMessageRequest, strictSender = false }) {
    let _unsub = null;
    let _draining = null;
    let _drainingGeneration = 0;
    let _drainAgain = false;
    let _starting = null;
    let _generation = 0;
    // A decrypt spends its ratchet key even if deleting the drop fails. Remember
    // consumed URLs for this engine's lifetime so retries only acknowledge them.
    // Retain failed acknowledgements; evict successful ones first at the cap.
    const _consumed = new Map();
    const MAX_CONSUMED = 1024;

    async function _acknowledge(url) {
        _consumed.set(url, false);
        const ok = await pod.podDeleteDmDrop(url);
        if (ok) _consumed.set(url, true);
    }

    function _hasReceiptSpace() {
        if (_consumed.size < MAX_CONSUMED) return true;
        for (const [url, acknowledged] of _consumed) {
            if (acknowledged) { _consumed.delete(url); return true; }
        }
        // Apply backpressure rather than forgetting a spent key during a long
        // deletion outage. The untouched inbox entries remain available later.
        return false;
    }

    // Drop an (already-encrypted) DM to the recipient's pod inbox.
    async function dropDm(payload) {
        const to = payload.target_webid || payload.to_webid;
        if (!to) return false;
        const root = peerPodRootFromWebId(to);
        if (!root) return false;
        const env = envelopeFromDmPayload(payload, getSelfWebId(), getDisplayName ? getDisplayName() : '');
        // R107: sign the envelope so the recipient can confirm it really came from
        // this WebID's owner (best effort — an unsigned drop still delivers).
        if (signEnvelope) { try { const _s = await signEnvelope(env); if (_s) Object.assign(env, _s); } catch { /* deliver unsigned */ } }
        return pod.podDropDm(root, env);
    }

    // Multi-device send: one per-device encrypted entry per recipient device (and
    // our own other devices). Each entry goes to that WebID's pod inbox, tagged
    // with to_device_id so the right device claims it (devices share a pod inbox).
    async function dropFanout(cmd) {
        const entries = cmd && cmd.fanout;
        if (!Array.isArray(entries)) return false;
        // Delivery is only real when a copy reaches one of the RECIPIENT's devices.
        // The fanout also carries self-sync copies to our own other devices; those
        // land in our own inbox and must not be counted as delivery, or a DM that
        // reached none of the peer's devices would falsely clear its pending state.
        const self = getSelfWebId();
        let anyRecipientOk = false;
        for (const e of entries) {
            if (!e || !e.to_webid) continue;
            const root = peerPodRootFromWebId(e.to_webid);
            if (!root) continue;
            const envelope = {
                v: 1, kind: 'fanout', from_webid: self,
                message_id: cmd.message_id, to_device_id: e.to_device_id, payload: e.payload,
            };
            // R107: sign each fanout copy (bound to its to_device_id) so the peer's
            // device can verify it, same as a single-send DM.
            if (signEnvelope) { try { const _s = await signEnvelope(envelope); if (_s) Object.assign(envelope, _s); } catch { /* deliver unsigned */ } }
            const ok = await pod.podDropDm(root, envelope);
            if (ok && e.to_webid !== self) anyRecipientOk = true;
        }
        return anyRecipientOk;
    }

    async function _senderStatus(env) {
        if (!verifySender) return 'rejected';
        try { return _verifyStatus(await verifySender(env)); } catch { return 'unavailable'; }
    }

    // A drop that claims a sender we could not verify and that has no session to
    // decrypt under: it must not start one (the inbox is public-Append, so anyone
    // can drop an envelope naming any WebID). Tell the user it arrived without
    // opening it, and remove it.
    async function _rejectUnverified(url, env) {
        console.warn('[webdm] unverified first-contact drop from', env.from_webid, '- not opened');
        if (onUnverifiedDrop) { try { onUnverifiedDrop({ from_webid: env.from_webid, message_id: env.message_id }); } catch { /* notice is best effort */ } }
        await _acknowledge(url);
    }

    function _messageRequest(env) {
        try {
            onMessageRequest({
                from_webid: env.from_webid,
                from_display_name: typeof env.from_display_name === 'string' ? env.from_display_name.slice(0, 100) : '',
                message_id: env.message_id,
            });
        } catch { /* the request UI is best effort; the drop stays */ }
    }

    async function _handleEnvelope(url, env) {
        const from = env && env.from_webid;
        if (!from) return;
        // A blocked sender's drop is removed unread, before any verify or decrypt.
        if (isBlocked && isBlocked(from)) {
            if (env.kind !== 'fanout' || !getMyDeviceId || !env.to_device_id || env.to_device_id === getMyDeviceId()) {
                await _acknowledge(url);
            }
            return;
        }
        // Not a contact yet: a verified drop waits in the inbox as a message
        // request; anything unverified falls through to the first-contact rules.
        const _unknown = !!(isKnownSender && onMessageRequest && !isKnownSender(from));
        // Multi-device fanout copy: only the one addressed to THIS device is ours
        // (devices share a pod inbox). Leave a sibling's copy for that device to
        // claim. Reuse the app's dm_fanout handler for the per-device decrypt.
        if (env.kind === 'fanout') {
            if (getMyDeviceId && env.to_device_id && env.to_device_id !== getMyDeviceId()) return;
            const status = await _senderStatus(env);
            if (_unknown && status === 'verified') { _messageRequest(env); return; }
            const verified = status === 'verified';
            const p = env.payload || {};
            if (!verified && p.e2e) {
                // Same session id main.js uses for a fanout copy.
                const pid = from + '#' + (p.from_device_id || from);
                const known = e2e.hasSession ? await e2e.hasSession(pid) : false;
                if (!known) {
                    if (status === 'unavailable') return;   // retry once the signer is reachable
                    await _rejectUnverified(url, env);
                    return;
                }
            }
            await handleEvent({
                type: 'dm_fanout', to_device_id: env.to_device_id,
                from_webid: env.from_webid, message_id: env.message_id, payload: env.payload,
                sender_verified: verified,
                // Only a verified envelope may start a new ratchet session.
                allow_session_init: verified,
            });
            await _acknowledge(url);
            return;
        }
        // R107: is this envelope really signed by from_webid's owner? Checked
        // BEFORE decrypting, because decrypting with no session would create one
        // from the envelope's ratchet_pub.
        const status = await _senderStatus(env);
        const sender_verified = status === 'verified';
        if (_unknown && sender_verified) { _messageRequest(env); return; }
        if (strictSender && !sender_verified && !env.e2e) {
            if (status === 'unavailable') return;   // retry once the signer is reachable
            await _rejectUnverified(url, env);
            return;
        }
        let content = env.content;
        try {
            if (env.e2e) {
                // Do NOT cache the peer's long-term x25519_pub yet: an attacker can
                // drop an e2e envelope into our public-Append inbox, and caching an
                // unverified key here would pre-seed the trusted send-side key cache
                // (isE2EEnabled) so our first reply encrypts under the attacker's key.
                // Only a verified envelope may create the session. An unverified one
                // decrypts only under an existing session, whose keys only the peer
                // it was set up with can produce (the AEAD tag must verify, and a
                // failed decrypt leaves the stored state unchanged).
                content = await e2e.ratchetDecrypt(from, env.content, env.nonce, env.msg_num, env.ratchet_pub, env.pn || 0,
                    { allowInit: sender_verified });
            }
        } catch (err) {
            if (err && err.name === 'E2ENoSessionError') {
                if (status === 'unavailable') {
                    console.warn('[webdm] sender identity unreachable, leaving first-contact drop for retry');
                    return;
                }
                await _rejectUnverified(url, env);
                return;
            }
            // Leave an undecryptable drop in place for a later retry (bounded by
            // the inbox cap); never delete a drop we could not read.
            console.warn('[webdm] decrypt failed, leaving drop for retry:', err);
            return;
        }
        // Only now cache the sender's long-term x25519_pub (a signed field), and only
        // for a verified envelope: the send path (isE2EEnabled) trusts this cache to
        // skip the authenticated pod-key fetch, so an unverified key must never land.
        if (env.e2e && env.x25519_pub && sender_verified) e2e.cachePeerPub(from, env.x25519_pub);
        const message = {
            type: 'message',
            message_id: env.message_id,
            thread_id: from,
            from_webid: from,
            from_display_name: env.from_display_name || '',
            content,
            timestamp: env.timestamp || new Date().toISOString(),
            reply_to_id: env.reply_to_id || null,
            source: 'local_dm',
            local: true,
            _persistDm: true,
            sender_verified,
        };
        // Deliver live first: a successful decrypt has already advanced the ratchet,
        // so this message can never be re-decrypted — it must be shown now, and the
        // drop must go (retaining spent ciphertext buys nothing and would wedge the
        // inbox). Persist to local history as a best effort, awaited so it lands
        // before the drop is deleted, but NEVER gate delivery or deletion on it: a
        // cache write failure (quota, private mode) must not hide the message or
        // leave an undeletable, undecryptable drop.
        // The UI handler may be asynchronous. A failure must not bypass the
        // history write and acknowledgement after a successful decrypt.
        try { await handleEvent(message); }
        catch (err) { console.warn('[webdm] message handling failed:', err); }
        if (persistMessage) { try { await persistMessage(message); } catch { /* best-effort cache */ } }
        await _acknowledge(url);
    }

    async function _drain(generation) {
        const drops = await pod.podReadDmDrops();
        for (const d of drops) {
            if (generation !== _generation) return;
            if (!d || !d.url || !d.envelope) continue;
            // Isolate per-drop failures so one bad envelope cannot block the rest.
            try {
                if (_consumed.has(d.url)) {
                    if (!_consumed.get(d.url)) await _acknowledge(d.url);
                    continue;
                }
                if (_hasReceiptSpace()) await _handleEnvelope(d.url, d.envelope);
            }
            catch (err) { console.warn('[webdm] drop handling failed:', err); }
        }
    }

    // Coalesce notifications/reconnect/manual refresh into a single reader.
    // Requests during a read trigger a fresh scan, so a newly arrived drop isn't
    // lost simply because the current listing was captured before it arrived.
    function drainOnce() {
        if (_draining && _drainingGeneration !== _generation) {
            const generation = _generation;
            return _draining.catch(() => {}).then(() => {
                if (generation === _generation) return drainOnce();
            });
        }
        if (_draining) { _drainAgain = true; return _draining; }
        const generation = _generation;
        _drainingGeneration = generation;
        _draining = Promise.resolve().then(async () => {
            do {
                _drainAgain = false;
                await _drain(generation);
            } while (_drainAgain && generation === _generation);
        }).finally(() => { _draining = null; });
        return _draining;
    }

    // Subscribe before the initial listing so arrivals during startup trigger
    // another scan. Concurrent starts share initialization and one subscription.
    function start() {
        if (_starting) return _starting;
        const generation = _generation;
        const starting = Promise.resolve().then(async () => {
            const inbox = await pod.podEnsureDmInbox();
            if (generation !== _generation) return null;
            if (!_unsub && inbox && notify && notify.watchResource) {
                _unsub = notify.watchResource(inbox, () => {
                    if (generation === _generation) drainOnce().catch(() => {});
                });
            }
            await drainOnce();
            return generation === _generation ? inbox : null;
        }).finally(() => { if (_starting === starting) _starting = null; });
        _starting = starting;
        return starting;
    }

    function stop() {
        _generation++;
        _drainAgain = false;
        _starting = null;
        if (_unsub) { try { _unsub(); } catch { /* ignore */ } _unsub = null; }
    }

    return { dropDm, dropFanout, drainOnce, start, stop };
}
