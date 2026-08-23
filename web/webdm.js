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

export function createWebDm({ pod, e2e, notify, handleEvent, getSelfWebId, getDisplayName }) {
    let _unsub = null;

    // Drop an (already-encrypted) DM to the recipient's pod inbox.
    async function dropDm(payload) {
        const to = payload.target_webid || payload.to_webid;
        if (!to) return false;
        const root = peerPodRootFromWebId(to);
        if (!root) return false;
        const env = envelopeFromDmPayload(payload, getSelfWebId(), getDisplayName ? getDisplayName() : '');
        return pod.podDropDm(root, env);
    }

    async function _handleEnvelope(url, env) {
        const from = env && env.from_webid;
        if (!from) return;
        let content = env.content;
        try {
            if (env.e2e) {
                if (env.x25519_pub) e2e.cachePeerPub(from, env.x25519_pub);
                content = await e2e.ratchetDecrypt(from, env.content, env.nonce, env.msg_num, env.ratchet_pub, env.pn || 0);
            }
        } catch (err) {
            // Leave an undecryptable drop in place for a later retry (bounded by
            // the inbox cap); never delete a drop we could not read.
            console.warn('[webdm] decrypt failed, leaving drop for retry:', err);
            return;
        }
        handleEvent({
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
        });
        await pod.podDeleteDmDrop(url);
    }

    // Read and process every pending drop once.
    async function drainOnce() {
        const drops = await pod.podReadDmDrops();
        for (const d of drops) {
            if (d && d.envelope) await _handleEnvelope(d.url, d.envelope);
        }
    }

    // Ensure our inbox exists, drain what is already there, then watch for more.
    async function start() {
        const inbox = await pod.podEnsureDmInbox();
        await drainOnce();
        if (inbox && notify && notify.watchResource) {
            _unsub = notify.watchResource(inbox, () => { drainOnce().catch(() => {}); });
        }
        return inbox;
    }

    function stop() {
        if (_unsub) { try { _unsub(); } catch { /* ignore */ } _unsub = null; }
    }

    return { dropDm, drainOnce, start, stop };
}
