// Browser friendships: two actor-owned intents, request-specific decisions,
// durable delivery receipts, and polling independent of notification support.
import { friendWebId, validFriendIntent, friendshipState } from './friendio.js';
import { verifyFriendIntent } from './dmsig.js';
import { peerPodRootFromWebId } from './webdm.js';

export function createWebFriends({ io, pod, watchResource, isBlocked = () => false, onChange = () => {},
    pollMs = 10000, setTimer = setInterval, clearTimer = clearInterval }) {
    let generation = 0, active = false, captured = null, timer = null, unwatch = null, running = null;
    let ready = false, syncError = false, cursor = 0, first = true, inbox = null;
    const rows = new Map(), queues = new Map();
    const newest = (fresh, prior) => fresh && prior && fresh.updated < prior.updated ? prior : fresh;
    const live = token => active && token === generation && io.current(captured);
    const view = () => ({ ready, syncError, rows: [...rows.values()].filter(row => !isBlocked(row.peer)) });
    const emit = () => { try { onChange(view()); } catch { /* UI must not interrupt persistence */ } };
    function rowFor(peer) {
        const row = rows.get(peer);
        if (!ready) return { peer, state: 'loading' };
        if (!row) return { peer, state: 'none' };
        return { ...row, state: friendshipState(row.mine, row.theirs) };
    }
    async function ensureInbox(token) {
        if (inbox || !live(token)) return;
        try {
            const url = await pod.podEnsureFriendInbox();
            if (!live(token) || !url) return;
            inbox = url;
            if (watchResource) unwatch = watchResource(url, () => { refresh().catch(() => {}); });
        } catch { /* the retry timer stays active */ }
    }
    async function consume(token) {
        let failed = false;
        let drops;
        try { drops = await pod.podReadFriendDrops(); } catch { return true; }
        for (const { url, snapshot } of drops || []) {
            if (!live(token)) return;
            const peer = friendWebId(snapshot?.actor);
            let retain = false;
            if (peer && io.safePeer(peer, captured) && validFriendIntent(snapshot, peer, captured.webId)
                && await verifyFriendIntent(snapshot) && !isBlocked(peer)) {
                const fresh = await io.read(peer, false, captured);
                if (!live(token)) return;
                let trusted = fresh.ok && !!fresh.doc;
                if (!trusted) {
                    // A legitimate hint may race a temporarily unreadable ACL.
                    // Remember it only if the actor's published key authenticates it.
                    try {
                        const signer = await pod.podFetchPeerSigner(peerPodRootFromWebId(peer));
                        if (!signer) retain = true;
                        else trusted = snapshot.proof.split('|')[0] === signer.signer;
                    } catch { retain = true; }
                }
                if (trusted) {
                    const saved = await io.remember(peer, null, captured);
                    if (!live(token)) return;
                    if (!saved.ok) { failed = true; retain = true; }
                }
            }
            if (!live(token)) return;
            if (!retain) { try { await pod.podDeleteFriendDrop(url); } catch { /* idempotent on next listing */ } }
        }
        return failed;
    }
    async function deliver(peer, mine, delivered, token) {
        if (!mine || delivered?.revision === mine.revision || !live(token)) return delivered;
        if (!(await io.grant(peer, captured)) || !live(token)) return delivered;
        let sent = false;
        try { sent = await pod.podDropFriend(peerPodRootFromWebId(peer), mine); } catch { /* receipt remains pending */ }
        if (!live(token) || !sent) return delivered;
        const result = await io.remember(peer, mine, captured);
        return live(token) && result.ok ? { revision: mine.revision, updated: mine.updated } : delivered;
    }
    async function refreshOnce(token) {
        await ensureInbox(token);
        if (!live(token)) return;
        const inboxFailed = await consume(token);
        if (!live(token)) return;
        const index = await io.readIndex(captured);
        if (!live(token)) return;
        if (!index.ok) { syncError = true; emit(); return; }
        syncError = !!inboxFailed || !inbox; ready = true;
        const entries = index.entries;
        const allowed = new Set(entries.map(entry => entry.peer));
        for (const peer of rows.keys()) if (!allowed.has(peer)) rows.delete(peer);
        const count = first ? entries.length : Math.min(20, entries.length);
        const selected = Array.from({ length: count }, (_, i) => entries[(cursor + i) % entries.length]);
        cursor = entries.length ? (cursor + count) % entries.length : 0; first = false;
        // Bound fetch concurrency; large lists must not burst into pod rate limits.
        for (let offset = 0; offset < selected.length; offset += 4) {
            await Promise.all(selected.slice(offset, offset + 4).map(async entry => {
                const peer = entry.peer;
                const mine = await io.read(peer, true, captured), theirs = await io.read(peer, false, captured);
                if (!live(token)) return;
                const previous = rows.get(peer);
                let ownDoc = mine.ok ? newest(mine.doc, previous?.mine) : previous?.mine;
                const peerDoc = theirs.ok ? newest(theirs.doc, previous?.theirs) : previous?.theirs;
                if (mine.ok && theirs.ok && ownDoc) {
                    const offer = ownDoc.offer, decision = ownDoc.decision;
                    const closeOffer = offer?.state === 'pending' && peerDoc?.decision?.id === offer.id
                        && ['declined', 'removed'].includes(peerDoc.decision.state);
                    const closeDecision = decision?.state === 'accepted'
                        && (peerDoc?.offer?.id !== decision.id || peerDoc.offer.state !== 'pending');
                    if (closeOffer || closeDecision) {
                        const settled = await io.update(peer, latest => ({
                            offer: closeOffer && latest?.offer?.id === offer.id ? { ...latest.offer, state: 'withdrawn' } : latest?.offer,
                            decision: closeDecision && latest?.decision?.id === decision.id ? { ...latest.decision, state: 'removed' } : latest?.decision,
                        }), captured);
                        if (settled.ok) ownDoc = settled.doc;
                    }
                }
                const delivered = isBlocked(peer) ? entry.delivered : await deliver(peer, ownDoc, entry.delivered, token);
                if (!live(token)) return;
                const latest = rows.get(peer);
                rows.set(peer, { peer, mine: newest(ownDoc, latest?.mine) || null, theirs: newest(peerDoc, latest?.theirs) || null,
                    stale: !mine.ok || (!theirs.ok && !!previous?.theirs), queued: !!ownDoc && delivered?.revision !== ownDoc.revision });
            }));
            if (!live(token)) return;
        }
        emit();
    }
    function refresh() {
        if (!active || !io.current(captured)) return Promise.resolve();
        if (running) return running;
        const token = generation;
        const work = refreshOnce(token).catch(() => { if (live(token)) { syncError = true; emit(); } });
        running = work;
        return work.finally(() => { if (running === work) running = null; });
    }
    function action(peerValue, kind, expectedId = null) {
        const peer = friendWebId(peerValue), token = generation;
        const account = captured;
        if (!live(token) || !peer || !io.safePeer(peer, account)) return Promise.resolve({ ok: false, reason: 'invalid' });
        if (isBlocked(peer)) return Promise.resolve({ ok: false, reason: 'blocked' });
        const key = account.id + '\n' + peer;
        const prior = queues.get(key) || Promise.resolve();
        const work = prior.catch(() => {}).then(async () => {
            if (!live(token)) return { ok: false, reason: 'account_changed' };
            const saved = await io.update(peer, async mine => {
                if (!live(token)) return { reason: 'account_changed' };
                const offer = mine?.offer || null, decision = mine?.decision || null;
                if (kind === 'request') {
                    const fresh = await io.read(peer, false, account);
                    const theirs = fresh.ok ? fresh.doc : rows.get(peer)?.theirs;
                    if (friendshipState(mine, theirs) === 'friends') return { unchanged: true };
                    const negative = theirs?.decision?.id === offer?.id && ['declined', 'removed'].includes(theirs?.decision?.state);
                    if (offer?.state === 'pending' && !negative) return { unchanged: true };
                    return { offer: { id: crypto.randomUUID(), state: 'pending' }, decision };
                }
                if (kind === 'withdraw') {
                    if (offer?.id !== expectedId) return { reason: 'request_changed' };
                    if (offer.state !== 'pending') return { unchanged: true };
                    return { offer: { ...offer, state: 'withdrawn' }, decision };
                }
                if (kind === 'remove') {
                    const fresh = await io.read(peer, false, account);
                    const peerOffer = (fresh.ok ? fresh.doc : rows.get(peer)?.theirs)?.offer;
                    return { offer: offer ? { ...offer, state: 'removed' } : null,
                        decision: peerOffer ? { id: peerOffer.id, state: 'removed' } : decision ? { ...decision, state: 'removed' } : null };
                }
                const peerState = await io.read(peer, false, account);
                if (!peerState.ok) return { reason: 'unavailable' };
                if (peerState.doc?.offer?.state !== 'pending' || peerState.doc.offer.id !== expectedId) return { reason: 'request_changed' };
                const desired = kind === 'accept' ? 'accepted' : 'declined';
                if (decision?.id === expectedId && decision.state !== desired) return { reason: 'request_changed' };
                return { offer, decision: { id: expectedId, state: desired } };
            }, account);
            if (!live(token)) return { ok: false, reason: 'account_changed' };
            if (saved.ok && saved.doc) {
                const theirs = await io.read(peer, false, account);
                if (!live(token)) return { ok: false, reason: 'account_changed' };
                const old = rows.get(peer);
                const delivered = await deliver(peer, saved.doc, null, token);
                if (!live(token)) return { ok: false, reason: 'account_changed' };
                rows.set(peer, { peer, mine: saved.doc, theirs: theirs.ok ? theirs.doc : old?.theirs,
                    stale: !theirs.ok, queued: delivered?.revision !== saved.doc.revision });
                emit();
                if (kind === 'accept' && (!theirs.ok || theirs.doc?.offer?.id !== expectedId || theirs.doc.offer.state !== 'pending')) {
                    return { ok: false, reason: 'request_changed' };
                }
            } else await refresh();
            return saved;
        });
        queues.set(key, work);
        work.finally(() => { if (queues.get(key) === work) queues.delete(key); }).catch(() => {});
        return work;
    }
    function stop() {
        generation++; active = false; ready = false; rows.clear(); running = null; inbox = null;
        if (timer !== null) { clearTimer(timer); timer = null; }
        if (unwatch) { try { unwatch(); } catch { /* cleanup */ } unwatch = null; }
        emit();
    }
    async function start() {
        stop(); active = true; captured = io.session(); first = true; cursor = 0;
        if (!captured) { active = false; return; }
        timer = setTimer(() => { refresh().catch(() => {}); }, pollMs);
        const token = generation;
        await io.prepare(captured);
        if (live(token)) await refresh();
    }
    function resume() { return !active || !io.current(captured) ? start() : refresh(); }
    return { start, stop, resume, refresh, stateFor: rowFor, view, sessionId: () => captured?.id,
        request: peer => action(peer, 'request'), accept: (peer, id) => action(peer, 'accept', id),
        decline: (peer, id) => action(peer, 'decline', id), withdraw: (peer, id) => action(peer, 'withdraw', id), remove: peer => action(peer, 'remove') };
}
