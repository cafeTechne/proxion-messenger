import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { decodeNativeReaction } from './native-reaction-codec.js';
import { decodeNativeReply } from './native-reply-codec.js';
import { validNativeMessageRef } from './native-message-ref.js';

function validReplyPlan(job) {
    if (job.wireText === undefined) return !job.nativeControl && !job.record?.native_reaction;
    const reaction = job.nativeControl === 'reaction' ? decodeNativeReaction(job.wireText, job.id) : null;
    const body = reaction || decodeNativeReply(job.wireText, job.id);
    const ref = reaction ? job.record?.native_reaction?.ref : job.record?.native_reply_ref;
    const binding = reaction ? job.record?.content === '' && ['mid', 'tok', 'seq', 'op', 'emoji'].every(field => reaction[field] === job.record?.native_reaction?.[field])
        && job.record?.native_reaction_context?.actorWebId === job.actor
        && job.record?.native_reaction_context?.selfWebId === job.actor
        && job.record?.native_reaction_context?.peerWebId === job.peer
        && job.record?.native_reaction_context?.threadId === job.record?.thread_id
        : !job.nativeControl && body?.text === job.record?.content && job.record?.native_reply_actor === job.actor;
    return !!body && binding && validNativeMessageRef(ref)
        && ['v', 'kind', 'id', 'author'].every(field => ref[field] === body.ref[field])
        && job.record.from_webid === job.actor
        && String(job.actor).startsWith('did:key:') && String(job.peer).startsWith('did:key:') && job.actor !== job.peer
        && job.mode === 'fanout' && Array.isArray(job.entries) && job.entries.every(entry => !entry.primary
            && typeof entry.device === 'string' && entry.device && /^[A-Za-z0-9_-]{43}$/.test(entry.pub || ''))
        && (job.route?.cmd === 'local_dm' ? job.route.target_webid === job.peer
            : job.route?.cmd === 'send_dm' && job.route.cert_id === job.record.thread_id);
}


// A frozen send plan precedes every ratchet advance. Per-peer pendingSends then
// bridge a crash between encryption and saving the fully prepared wire frames.
export function createDmOutbox({ e2e, getScope, getGeneration = () => 0, isActive = () => true, send,
    persistMessage, clearDelivery, canSend = () => true, planStatus = () => 'ready', nativeReferencesReady = () => false, onCancelled = () => {}, onPending = () => {}, onConfirmed = () => {},
    onError = err => console.warn('[dm-outbox] pending:', err), now = Date.now }) {
    const failures = new Set(), headerWaiting = new Set();
    const rememberFailure = (id, err) => {
        if (err?.name === 'E2EHeaderPendingError') headerWaiting.add(id);
        else failures.add(id);
    };
    const jobs = new Map(), queues = new Map(), notified = new Set(), settled = new Set(), attempts = new Map();
    let generation = 0, timer = null, restoring = null;
    const context = () => {
        const scope = getScope(), epoch = generation, session = getGeneration();
        return { scope, current: () => generation === epoch && scope === getScope() && session === getGeneration() && isActive() };
    };
    function serialized(id, work) {
        const run = (queues.get(id) || Promise.resolve()).catch(() => {}).then(work);
        queues.set(id, run);
        run.finally(() => { if (queues.get(id) === run) queues.delete(id); }).catch(() => {});
        return run;
    }
    const accepted = job => new Set(job.accepted || []);
    function normalized(job) {
        if (job.receiptsVersion === 1) return job;
        // Old confirmed jobs proved peer acceptance only. If cleanup was
        // interrupted, preserve their mirrors rather than assuming acceptance.
        const known = job.confirmed ? job.entries.flatMap((entry, index) => entry.account === job.peer ? [index] : []) : [];
        return { ...job, receiptsVersion: 1, accepted: known, peerConfirmed: !!job.confirmed,
            confirmed: !!job.confirmed && known.length === job.entries.length };
    }
    const remaining = job => job.entries.filter((entry, index) => !accepted(job).has(index));
    function newer(live, snapshot) {
        if (!live) return snapshot;
        if ((live.revision || 0) !== (snapshot.revision || 0)) return (live.revision || 0) > (snapshot.revision || 0) ? live : snapshot;
        const stage = job => job.confirmed ? 10000 : (job.peerConfirmed ? 1000 : 0) + (job.frames ? 100 : 0) + accepted(job).size;
        return stage(live) >= stage(snapshot) ? live : snapshot;
    }
    async function save(job, ctx) {
        if (!ctx.current()) throw Error('Send context changed');
        job = { ...job, revision: Math.max(job.revision || 0, jobs.get(job.id)?.revision || 0) + 1 };
        await e2e.saveDmSendJob(job, ctx.current);
        if (!ctx.current()) throw Error('Send context changed');
        jobs.set(job.id, job);
    }
    function framesFor(job, encrypted) {
        const field = enc => ({ content: enc.ciphertext, nonce: enc.nonce, msg_num: enc.msgNum,
            pn: enc.pn, ratchet_pub: enc.ratchetPub, e2e: true });
        const fanout = [];
        for (let index = 0; index < job.entries.length; index++) {
            const entry = job.entries[index];
            if (entry.primary || !encrypted[index]) continue;
            fanout.push({ to_webid: entry.account, to_device_id: entry.device,
                payload: { ...field(encrypted[index]), from_device_id: job.device,
                    thread_id: job.record.thread_id, thread_peer: job.peer,
                    x25519_pub: job.pub, ...(job.wireText !== undefined ? { native_ref_v: 1, recipient_pub: entry.pub, e2e_v: 1 } : {}), ...(job.record.reply_to_id && job.wireText === undefined ? { reply_to_id: job.record.reply_to_id } : {}) } });
        }
        if (job.mode === 'fanout') return fanout.length ? [{ cmd: job.wireText !== undefined ? 'native_ref_send' : 'send_dm_fanout',
            ...(job.wireText !== undefined ? { native_ref_v: 1, cert_id: job.record.thread_id } : {}), message_id: job.id, fanout }] : [];
        const primary = job.entries.findIndex(entry => entry.primary);
        const route = { ...job.route };
        if (job.wireText !== undefined) delete route.reply_to_id;
        const frames = encrypted[primary] ? [{ ...route, ...field(encrypted[primary]), message_id: job.id,
            timestamp: job.record.timestamp, from_display_name: job.record.from_display_name }] : [];
        if (fanout.length) frames.push({ cmd: 'send_dm_fanout', message_id: job.id, fanout });
        return frames;
    }
    async function settleAccepted(job, ctx) {
        if (!ctx.current()) return false;
        if (job.peerConfirmed) {
            onConfirmed(job.id, job);
            if (await clearDelivery(job.id) === false || !ctx.current()) return false;
        }
        for (const index of accepted(job)) {
            const entry = job.entries[index];
            if (!entry || !ctx.current()) return false;
            if (job.wireText !== undefined && await e2e.hasPendingDmSend(entry.peerId, job.sendId)
                && await e2e.releaseDmSendHold(entry.peerId, job.sendId, ctx.current) === false) return false;
            if (!ctx.current() || await e2e.finishDmSend(entry.peerId, job.sendId, ctx.current) === false) return false;
        }
        return ctx.current();
    }
    async function cleanup(job, ctx) {
        if (!await settleAccepted(job, ctx)) return false;
        if (job.media && !job.mediaReleased) return true; // draft/history owns final release
        if (!ctx.current() || await e2e.removeDmSendJob(job.id, ctx.current) === false) return false;
        jobs.delete(job.id); attempts.delete(job.id); notified.delete(job.id);
        settled.add(job.id);
        if (settled.size > 512) settled.delete(settled.values().next().value);
        return true;
    }
    function pendingFrames(job, include = () => true) {
        const pending = remaining(job).filter(entry => include(entry) && planStatus({ ...job, entries: [entry] }) === 'ready');
        return job.frames.flatMap(frame => {
            if (!['send_dm_fanout', 'native_ref_send'].includes(frame.cmd)) return pending.some(entry => entry.primary) ? [frame] : [];
            const fanout = frame.fanout.filter(target => pending.some(entry => !entry.primary
                && entry.account === target.to_webid && entry.device === target.to_device_id));
            return fanout.length ? [{ ...frame, fanout }] : [];
        });
    }
    const intentQueues = new Map();
    function queue(input, { reuseMedia = false } = {}) {
        const ctx = context();
        const key = JSON.stringify([ctx.scope, generation, getGeneration()]);
        const run = (intentQueues.get(key) || Promise.resolve()).catch(() => {}).then(() => {
            if (!ctx.current()) throw Error('Send context changed');
            return queueIntent(input, reuseMedia);
        });
        intentQueues.set(key, run);
        run.finally(() => { if (intentQueues.get(key) === run) intentQueues.delete(key); }).catch(() => {});
        return run;
    }
    async function queueIntent(input, reuseMedia) {
        const ctx = context();
        if (!ctx.current()) throw Error('Send context changed');
        const existing = (await e2e.listDmSendJobs()).filter(job => job.scope === ctx.scope);
        if (!ctx.current()) throw Error('Send context changed');
        const prior = existing.find(job => job.id === input.id);
        if (prior) {
            if (!reuseMedia || !input.media || !prior.media || prior.peer !== input.peer
                    || prior.actor !== input.actor || prior.device !== input.device
                    || prior.media.draftKey !== input.media.draftKey || prior.record.content !== input.record.content) throw Error('Send job already exists');
            const job = newer(jobs.get(prior.id), normalized(prior));
            jobs.set(job.id, job); return job;
        }
        if (existing.length >= 128) throw Error('DM outbox is full');
        if (!input.entries?.length || !input.entries.some(entry => entry.account === input.peer)) throw Error('Missing recipient');
        if (!['single', 'fanout'].includes(input.mode) || input.entries.some(entry => typeof entry.peerId !== 'string' || !entry.peerId)
                || !input.record?.thread_id || input.record.message_id !== input.id || typeof input.record.content !== 'string'
                || (input.mode === 'single' && input.entries.filter(entry => entry.primary).length !== 1)) {
            throw Error('Invalid frozen send plan');
        }
        if (!validReplyPlan(input)) throw Error('Invalid native reply send plan');
        if (input.wireText !== undefined && !nativeReferencesReady(input)) throw Error('Native reference delivery unavailable');
        const job = JSON.parse(JSON.stringify({ ...input, receiptsVersion: 1, accepted: [], peerConfirmed: false, scope: ctx.scope,
            sendId: await dmEnvelopeFingerprint({ scope: ctx.scope, message_id: input.id }) }));
        await save(job, ctx);
        notified.add(job.id); // the compose path renders/tracks its optimistic copy
        return job;
    }
    function preparedResults(job) {
        const result = { ...job.prepared };
        job.entries.forEach((entry, index) => {
            const payload = entry.primary ? job.frames?.find(frame => frame.cmd !== 'send_dm_fanout')
                : job.frames?.flatMap(frame => frame.fanout || []).find(target => target.to_webid === entry.account && target.to_device_id === entry.device)?.payload;
            if (payload) result[index] = { ciphertext: payload.content, nonce: payload.nonce,
                msgNum: payload.msg_num, pn: payload.pn, ratchetPub: payload.ratchet_pub };
        });
        return result;
    }
    function transmit(id, { force = true } = {}) {
        const ctx = context();
        return serialized(id, async () => {
            const job = jobs.get(id);
            if (!job || job.scope !== ctx.scope || !ctx.current()) return false;
            if (job.confirmed) return cleanup(job, ctx);
            if (!force && attempts.has(id) && now() - attempts.get(id) < 18000) return false;
            try {
                if (job.peerConfirmed) await settleAccepted(job, ctx);
                if (!ctx.current() || !canSend(job) || job.wireText !== undefined && !nativeReferencesReady(job)) return false;
                failures.delete(id);
                headerWaiting.delete(id);
                // A reaction's source state must commit before its first header.
                // Replaying older intents is harmless: durable sequence state wins.
                if (job.nativeControl && (!validReplyPlan(job) || await persistMessage(job.record, ctx.current) === false || !ctx.current())) return false;
                let ready = job, transmitted = false;
                const encrypted = preparedResults(job);
                const prepare = async (entry, index) => {
                    if (encrypted[index] || accepted(ready).has(index)) return;
                    if (!ctx.current() || job.wireText !== undefined && !nativeReferencesReady(job)) throw Error('Send context changed');
                    if (!validReplyPlan(job)) throw Error('Invalid native reply send plan');
                    encrypted[index] = await e2e.ratchetEncrypt(entry.peerId, job.wireText ?? job.record.content,
                        { sendId: job.sendId, peerPub: entry.pub, current: () => ctx.current() && (job.wireText === undefined || nativeReferencesReady(job)), ...(job.wireText !== undefined ? { holdForReceive: true } : {}) });
                };
                if (!job.frames) {
                    const peers = job.entries.filter(entry => entry.account === job.peer);
                    if (planStatus({ ...job, entries: peers }) !== 'ready') return false;
                    for (let index = 0; index < job.entries.length; index++) {
                        if (job.entries[index].account === job.peer) await prepare(job.entries[index], index);
                    }
                    ready = { ...job, frames: framesFor(job, encrypted) };
                    await save(ready, ctx); // peer ciphertext is durable before transmission
                }
                const transmitPhase = async include => {
                    if (!ctx.current() || !canSend(ready) || ready.wireText !== undefined && !nativeReferencesReady(ready)) return;
                    for (const frame of pendingFrames(ready, include)) {
                        if (!ctx.current() || ready.wireText !== undefined && !nativeReferencesReady(ready)) return;
                        attempts.set(id, now());
                        await send(JSON.parse(JSON.stringify(frame)), ready); transmitted = true;
                    }
                };
                if (!ctx.current() || (!job.nativeControl && !ready.peerConfirmed && await persistMessage(ready.record, ctx.current) === false) || !ctx.current()) return false;
                // Peer delivery precedes mirror preparation. A full/broken own
                // journal must not strand an otherwise valid primary send.
                await transmitPhase(entry => entry.account === job.peer);
                for (let index = 0; index < job.entries.length; index++) {
                    const entry = job.entries[index];
                    if (entry.account === job.peer || encrypted[index] || accepted(ready).has(index)
                            || planStatus({ ...ready, entries: [entry] }) !== 'ready') continue;
                    try {
                        await prepare(entry, index);
                        const next = { ...ready, frames: framesFor(job, encrypted) };
                        await save(next, ctx); ready = next;
                    } catch (err) {
                        if (!ctx.current()) throw err;
                        rememberFailure(id, err); onError(err);
                    }
                }
                await transmitPhase(entry => entry.account !== job.peer);
                return transmitted;
            } catch (err) { if (ctx.current()) rememberFailure(id, err); onError(err); return false; }
        });
    }
    function evidencedTargets(job, evidence) {
        if (job.wireText !== undefined && (evidence?.kind !== 'native-ref' || evidence.native_ref_v !== 1)) return [];
        if (['echo', 'history', 'pod'].includes(evidence?.kind)) {
            if (evidence.from !== job.record.from_webid || job.mode !== 'single') return [];
            return job.entries.flatMap((entry, index) => entry.primary ? [index] : []);
        }
        if ((job.wireText !== undefined ? evidence?.kind !== 'native-ref' : evidence?.kind !== 'fanout') || !Array.isArray(evidence.delivered)) return [];
        const prepared = preparedResults(job);
        return job.entries.flatMap((entry, index) => prepared[index] && !entry.primary && evidence.delivered.some(row =>
            row.to_webid === entry.account && row.to_device_id === entry.device) ? [index] : []);
    }
    function confirm(id, evidence) {
        const ctx = context();
        return serialized(id, async () => {
            const job = jobs.get(id);
            if (!job || !ctx.current() || job.scope !== ctx.scope || !job.frames) return false;
            const actor = planStatus({ ...job, entries: [] });
            if (actor === 'account' || actor === 'actor_unknown') return false;
            const targets = evidencedTargets(job, evidence);
            if (!targets.length) return false;
            const known = accepted(job); for (const index of targets) known.add(index);
            const peerConfirmed = job.entries.every((entry, index) => entry.account !== job.peer || known.has(index));
            const confirmed = { ...job, accepted: [...known], peerConfirmed, confirmed: known.size === job.entries.length };
            try {
                await save(confirmed, ctx); // every accepted target is durable before cleanup
                if (confirmed.confirmed) await cleanup(confirmed, ctx);
                else await settleAccepted(confirmed, ctx);
                return peerConfirmed;
            } catch (err) { onError(err); return false; }
        });
    }
    async function inspect() {
        const ctx = context();
        if (!ctx.current()) return [];
        const snapshots = await e2e.listDmSendJobs();
        if (!ctx.current()) return [];
        const rows = [];
        for (const row of snapshots) {
            const snapshot = normalized(row);
            if (snapshot.scope !== ctx.scope || settled.has(snapshot.id)) continue;
            const live = jobs.get(snapshot.id);
            const job = newer(live, snapshot);
            jobs.set(job.id, job);
            let canCancel = !job.nativeControl && !job.media && !job.frames && !job.confirmed && !!e2e.hasPendingDmSend;
            if (canCancel) for (const entry of job.entries) {
                if (await e2e.hasPendingDmSend(entry.peerId, job.sendId)) { canCancel = false; break; }
            }
            if (!ctx.current()) return [];
            if (settled.has(job.id)) continue;
            // A live prepare/confirmation can overtake the scan's read awaits.
            const latest = jobs.get(job.id) || job;
            if (latest.nativeControl || latest.frames || latest.confirmed) canCancel = false;
            const plan = planStatus({ ...latest, entries: remaining(latest) });
            const state = plan === 'actor_unknown' ? 'unbound' : plan === 'account'
                ? latest.media ? 'mediaAttention' : latest.peerConfirmed ? 'accountConfirmed' : 'account'
                : latest.media && latest.peerConfirmed && !latest.mediaReleased ? 'media'
                    : latest.confirmed ? 'cleanup' : latest.peerConfirmed ? (plan === 'changed' ? 'syncAttention' : 'sync')
                        : plan === 'changed' ? 'attention' : headerWaiting.has(job.id) ? 'nativeHeader' : failures.has(job.id) ? 'storage'
                            : !canSend(latest) || plan !== 'ready' ? 'waiting' : latest.frames ? 'confirmation' : 'waiting';
            const canRetry = remaining(latest).some(entry => planStatus({ ...latest, entries: [entry] }) === 'ready');
            rows.push({ id: latest.id, peer: latest.peer, timestamp: latest.record.timestamp, state, canCancel, canRetry });
        }
        return rows;
    }
    function cancel(id) {
        const ctx = context();
        return serialized(id, async () => {
            if (!ctx.current()) return false;
            const job = jobs.get(id) || (await e2e.listDmSendJobs()).find(row => row.id === id && row.scope === ctx.scope);
            if (!ctx.current() || !job || job.nativeControl || job.media || job.frames || job.confirmed || !e2e.hasPendingDmSend) return false;
            for (const entry of job.entries) {
                if (!ctx.current() || await e2e.hasPendingDmSend(entry.peerId, job.sendId)) return false;
            }
            if (!ctx.current() || await e2e.removeDmSendJob(id, ctx.current) === false || !ctx.current()) return false;
            settled.add(id); if (settled.size > 512) settled.delete(settled.values().next().value);
            jobs.delete(id); notified.delete(id); attempts.delete(id); failures.delete(id);
            onCancelled(id);
            return true;
        });
    }
    async function find(id) {
        const ctx = context(); if (!ctx.current()) return null;
        const live = jobs.get(id);
        if (live?.scope === ctx.scope) return live;
        const row = (await e2e.listDmSendJobs()).find(job => job.id === id && job.scope === ctx.scope);
        if (!ctx.current() || !row || settled.has(id)) return null;
        const job = newer(jobs.get(id), normalized(row)); jobs.set(id, job); return job;
    }
    function peerConfirmed(id) {
        const job = jobs.get(id);
        return job?.scope === getScope() && !!job.peerConfirmed;
    }
    async function waitForPeer(id, current = () => true, timeoutMs = 10000) {
        const ctx = context(), end = performance.now() + timeoutMs;
        while (ctx.current() && current()) {
            if (peerConfirmed(id)) return true;
            if (performance.now() >= end) return false;
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        return false;
    }
    function releaseMedia(id) {
        const ctx = context();
        return serialized(id, async () => {
            const job = jobs.get(id);
            if (!ctx.current() || !job?.media || !job.peerConfirmed) return false;
            const released = { ...job, mediaReleased: true };
            await save(released, ctx);
            if (released.confirmed) await cleanup(released, ctx);
            return true;
        });
    }
    function restore({ force = false } = {}) {
        if (restoring) return restoring;
        const ctx = context();
        const run = (async () => {
            if (!ctx.current()) return;
            for (const row of await e2e.listDmSendJobs()) {
                const snapshot = normalized(row);
                if (!ctx.current()) return;
                if (snapshot.scope !== ctx.scope || settled.has(snapshot.id)) continue;
                // A disk scan may have started before prepare/confirm committed.
                // Never roll a live job back to an earlier stage while restoring.
                const live = jobs.get(snapshot.id);
                const job = newer(live, snapshot);
                jobs.set(job.id, job);
                if (!job.confirmed && !job.peerConfirmed && !notified.has(job.id)) {
                    notified.add(job.id); onPending(job);
                }
                await transmit(job.id, { force });
            }
        })();
        restoring = run;
        run.finally(() => { if (restoring === run) restoring = null; }).catch(() => {});
        return run;
    }
    return {
        queue, send: transmit, confirm, restore, inspect, cancel, find, peerConfirmed, waitForPeer, releaseMedia,
        has: id => jobs.has(id) && jobs.get(id).scope === getScope(),
        record: id => jobs.get(id)?.scope === getScope() ? jobs.get(id).record : null,
        start() {
            if (!timer) timer = setInterval(() => restore().catch(onError), 5000);
            return restore({ force: true }).catch(onError);
        },
        stop() { generation++; if (timer) clearInterval(timer); timer = null; jobs.clear(); intentQueues.clear(); failures.clear(); headerWaiting.clear(); notified.clear(); settled.clear(); attempts.clear(); restoring = null; },
    };
}
