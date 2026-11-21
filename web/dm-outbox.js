import { dmEnvelopeFingerprint } from './dm-receipts.js';

// A frozen send plan precedes every ratchet advance. Per-peer pendingSends then
// bridge a crash between encryption and saving the fully prepared wire frames.
export function createDmOutbox({ e2e, getScope, getGeneration = () => 0, isActive = () => true, send,
    persistMessage, clearDelivery, canSend = () => true, planStatus = () => 'ready', onCancelled = () => {}, onPending = () => {}, onConfirmed = () => {},
    onError = err => console.warn('[dm-outbox] pending:', err), now = Date.now }) {
    const failures = new Set();
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
    async function save(job, ctx) {
        if (!ctx.current()) throw Error('Send context changed');
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
            if (entry.primary) continue;
            fanout.push({ to_webid: entry.account, to_device_id: entry.device,
                payload: { ...field(encrypted[index]), from_device_id: job.device,
                    thread_id: job.record.thread_id, thread_peer: job.peer,
                    x25519_pub: job.pub, ...(job.record.reply_to_id ? { reply_to_id: job.record.reply_to_id } : {}) } });
        }
        if (job.mode === 'fanout') return [{ cmd: 'send_dm_fanout', message_id: job.id, fanout }];
        const primary = job.entries.findIndex(entry => entry.primary);
        const frames = [{ ...job.route, ...field(encrypted[primary]), message_id: job.id,
            timestamp: job.record.timestamp, from_display_name: job.record.from_display_name }];
        if (fanout.length) frames.push({ cmd: 'send_dm_fanout', message_id: job.id, fanout });
        return frames;
    }
    async function cleanup(job, ctx) {
        if (!ctx.current()) return false;
        onConfirmed(job.id);
        if (await clearDelivery(job.id) === false || !ctx.current()) return false;
        for (const entry of job.entries) {
            if (!ctx.current() || await e2e.finishDmSend(entry.peerId, job.sendId, ctx.current) === false) return false;
        }
        if (!ctx.current() || await e2e.removeDmSendJob(job.id, ctx.current) === false) return false;
        jobs.delete(job.id); attempts.delete(job.id); notified.delete(job.id);
        settled.add(job.id);
        if (settled.size > 512) settled.delete(settled.values().next().value);
        return true;
    }
    const intentQueues = new Map();
    function queue(input) {
        const ctx = context();
        const key = JSON.stringify([ctx.scope, generation, getGeneration()]);
        const run = (intentQueues.get(key) || Promise.resolve()).catch(() => {}).then(() => {
            if (!ctx.current()) throw Error('Send context changed');
            return queueIntent(input);
        });
        intentQueues.set(key, run);
        run.finally(() => { if (intentQueues.get(key) === run) intentQueues.delete(key); }).catch(() => {});
        return run;
    }
    async function queueIntent(input) {
        const ctx = context();
        if (!ctx.current()) throw Error('Send context changed');
        const existing = (await e2e.listDmSendJobs()).filter(job => job.scope === ctx.scope);
        if (existing.some(job => job.id === input.id)) throw Error('Send job already exists');
        if (existing.length >= 128) throw Error('DM outbox is full');
        if (!input.entries?.length || !input.entries.some(entry => entry.account === input.peer)) throw Error('Missing recipient');
        if (!['single', 'fanout'].includes(input.mode) || input.entries.some(entry => typeof entry.peerId !== 'string' || !entry.peerId)
                || !input.record?.thread_id || input.record.message_id !== input.id || typeof input.record.content !== 'string'
                || (input.mode === 'single' && input.entries.filter(entry => entry.primary).length !== 1)) {
            throw Error('Invalid frozen send plan');
        }
        const job = JSON.parse(JSON.stringify({ ...input, scope: ctx.scope,
            sendId: await dmEnvelopeFingerprint({ scope: ctx.scope, message_id: input.id }) }));
        await save(job, ctx);
        notified.add(job.id); // the compose path renders/tracks its optimistic copy
        return job;
    }
    function transmit(id, { force = true } = {}) {
        const ctx = context();
        return serialized(id, async () => {
            const job = jobs.get(id);
            if (!job || job.scope !== ctx.scope || !ctx.current()) return false;
            if (job.confirmed) return cleanup(job, ctx);
            if (!force && attempts.has(id) && now() - attempts.get(id) < 18000) return false;
            try {
                if (!canSend(job) || planStatus(job) !== 'ready') return false;
                failures.delete(id);
                let ready = job;
                if (!job.frames) {
                    const encrypted = [];
                    for (const entry of job.entries) {
                        if (!ctx.current()) return false;
                        encrypted.push(await e2e.ratchetEncrypt(entry.peerId, job.record.content,
                            { sendId: job.sendId, peerPub: entry.pub, current: ctx.current }));
                    }
                    ready = { ...job, frames: framesFor(job, encrypted) };
                    await save(ready, ctx); // no frame may leave before this commit
                }
                if (!ctx.current() || await persistMessage(ready.record) === false || !ctx.current()) return false;
                if (!canSend(ready)) return false;
                attempts.set(id, now());
                for (const frame of ready.frames) {
                    if (!ctx.current()) return false;
                    await send(JSON.parse(JSON.stringify(frame)));
                }
                return true; // transmitted/queued is still not delivery confirmation
            } catch (err) { if (ctx.current()) failures.add(id); onError(err); return false; }
        });
    }
    function validEvidence(job, evidence) {
        if (evidence?.kind === 'pod') return evidence.from === job.record.from_webid;
        if (job.mode === 'single') return ['echo', 'history'].includes(evidence?.kind) && evidence.from === job.record.from_webid;
        if (evidence?.kind !== 'fanout' || !Array.isArray(evidence.delivered)) return false;
        return job.entries.filter(entry => entry.account === job.peer).every(entry =>
            evidence.delivered.some(row => row.to_webid === entry.account && row.to_device_id === entry.device));
    }
    function confirm(id, evidence) {
        const ctx = context();
        return serialized(id, async () => {
            const job = jobs.get(id);
            if (!job || !ctx.current() || job.scope !== ctx.scope || !job.frames || !validEvidence(job, evidence)) return false;
            const confirmed = { ...job, confirmed: true };
            try {
                await save(confirmed, ctx); // a crash during cleanup must not send again
                await cleanup(confirmed, ctx);
                return true;
            } catch (err) { onError(err); return false; }
        });
    }
    async function inspect() {
        const ctx = context();
        if (!ctx.current()) return [];
        const snapshots = await e2e.listDmSendJobs();
        if (!ctx.current()) return [];
        const rows = [];
        for (const snapshot of snapshots) {
            if (snapshot.scope !== ctx.scope || settled.has(snapshot.id)) continue;
            const live = jobs.get(snapshot.id);
            const stage = job => job.confirmed ? 2 : job.frames ? 1 : 0;
            const job = live && stage(live) >= stage(snapshot) ? live : snapshot;
            jobs.set(job.id, job);
            let canCancel = !job.frames && !job.confirmed && !!e2e.hasPendingDmSend;
            if (canCancel) for (const entry of job.entries) {
                if (await e2e.hasPendingDmSend(entry.peerId, job.sendId)) { canCancel = false; break; }
            }
            if (!ctx.current()) return [];
            if (settled.has(job.id)) continue;
            // A live prepare/confirmation can overtake the scan's read awaits.
            const latest = jobs.get(job.id) || job;
            if (latest.frames || latest.confirmed) canCancel = false;
            const plan = planStatus(latest);
            const state = latest.confirmed ? 'cleanup' : plan === 'changed' ? 'attention'
                : failures.has(job.id) ? 'storage' : !canSend(latest) || plan !== 'ready' ? 'waiting'
                    : latest.frames ? 'confirmation' : 'waiting';
            rows.push({ id: latest.id, peer: latest.peer, timestamp: latest.record.timestamp, state, canCancel });
        }
        return rows;
    }
    function cancel(id) {
        const ctx = context();
        return serialized(id, async () => {
            if (!ctx.current()) return false;
            const job = jobs.get(id) || (await e2e.listDmSendJobs()).find(row => row.id === id && row.scope === ctx.scope);
            if (!ctx.current() || !job || job.frames || job.confirmed || !e2e.hasPendingDmSend) return false;
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
    function restore({ force = false } = {}) {
        if (restoring) return restoring;
        const ctx = context();
        const run = (async () => {
            if (!ctx.current()) return;
            for (const snapshot of await e2e.listDmSendJobs()) {
                if (!ctx.current()) return;
                if (snapshot.scope !== ctx.scope || settled.has(snapshot.id)) continue;
                // A disk scan may have started before prepare/confirm committed.
                // Never roll a live job back to an earlier stage while restoring.
                const live = jobs.get(snapshot.id);
                const stage = job => job.confirmed ? 2 : job.frames ? 1 : 0;
                const job = live && stage(live) >= stage(snapshot) ? live : snapshot;
                jobs.set(job.id, job);
                if (!job.confirmed && !notified.has(job.id)) {
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
        queue, send: transmit, confirm, restore, inspect, cancel,
        has: id => jobs.has(id) && jobs.get(id).scope === getScope(),
        record: id => jobs.get(id)?.scope === getScope() ? jobs.get(id).record : null,
        start() {
            if (!timer) timer = setInterval(() => restore().catch(onError), 5000);
            return restore({ force: true }).catch(onError);
        },
        stop() { generation++; if (timer) clearInterval(timer); timer = null; jobs.clear(); intentQueues.clear(); failures.clear(); notified.clear(); settled.clear(); attempts.clear(); restoring = null; },
    };
}
