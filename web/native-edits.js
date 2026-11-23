import { createNativeEditSender } from './native-edit-send.js';
import { createNativeEditReceiver } from './native-edit-receive.js';
import { decodeNativeEdit } from './native-edit-codec.js';

export function createNativeEdits({ e2e, receipts, getScope, getGeneration = () => 0, isActive, send,
    contextForView, contextForPeer, getPeerPub, getOwnPub, getMessage, applyEdit, onApplied, onError = () => {} }) {
    let version = 0, epoch = 0, timer = null, recovering = null;
    const pending = new Map();
    const active = () => version === 1 && isActive();
    const generation = () => JSON.stringify([epoch, getGeneration()]);
    const guardedSend = (command, current = active) => active() && current() ? send(command) : false;
    function capability(request, current) {
        if (!active() || !current() || pending.size >= 10) return Promise.resolve(null);
        return new Promise(resolve => {
            const id = crypto.randomUUID();
            const finish = result => {
                const row = pending.get(id);
                if (!row) return;
                clearTimeout(row.timeout); pending.delete(id); resolve(result);
            };
            const timeout = setTimeout(() => finish(null), 6000);
            pending.set(id, { request, current, finish, timeout });
            try { if (guardedSend({ cmd: 'native_edit_capability', v: 1, request_id: id,
                cert_id: request.cert_id, file_id: request.file_id, expected_pub: request.expected_pub }, current) === false) finish(null); }
            catch (error) { finish(null); onError(error); }
        });
    }
    const sender = createNativeEditSender({ e2e, getScope, getGeneration: generation, isActive: active, isInspectable: isActive,
        contextForView, getPeerPub, getMessage, capability, send: guardedSend, applyEdit, onApplied });
    const receiver = createNativeEditReceiver({ e2e, receipts, getScope, getGeneration: generation, isActive: active,
        getOwnPub, contextForPeer, applyEdit, onApplied, send: guardedSend, onError });
    async function recover() {
        if (!active()) return false;
        if (recovering) return recovering;
        const stamp = generation();
        const run = (async () => {
            await receiver.recover();
            if (!active() || stamp !== generation()) return false;
            return sender.recover();
        })();
        recovering = run;
        try { return await run; } finally { if (recovering === run) recovering = null; }
    }
    function stop() {
        epoch++; version = 0;
        if (timer) clearInterval(timer); timer = null;
        for (const row of [...pending.values()]) row.finish(null);
        sender.stop(); receiver.stop(); recovering = null;
    }
    return { sender, receiver, recover, stop,
        start(negotiated) {
            stop(); version = negotiated === 1 ? 1 : 0;
            if (!active()) return;
            timer = setInterval(() => recover().catch(onError), 5000);
            recover().catch(onError);
        },
        async save(message, view, text) {
            if (!active()) return { ok: false, reason: 'unavailable' };
            try {
                const job = await sender.enqueue(message, view, text);
                // The durable intent owns retry even if the initial write fails.
                sender.retry(job.id).catch(onError);
                return { ok: true, message: { content: decodeNativeEdit(job.plaintext, job.event).text, edited_at: job.timestamp } };
            } catch (error) { onError(error); return { ok: false, reason: 'unavailable' }; }
        },
        async inspect() {
            if (!isActive()) return [];
            return (await sender.inspect()).map(row => ({ ...row, id: 'native-edit:' + row.id,
                canRetry: active() && row.state !== 'blocked', state: !active() ? 'nativeConnection'
                    : row.state === 'blocked' ? 'nativeBlocked' : row.state === 'history-pending' ? 'nativeHistory' : 'nativeHeader' }));
        },
        async handleEvent(event) {
            if (!active()) return false;
            if (event?.type === 'native_edit_capability_result') {
                const row = pending.get(event.request_id);
                if (row?.current() && event.v === 1 && event.native_edit_v === 1 && typeof event.supported === 'boolean'
                    && event.peer_webid === row.request.peer_webid && event.file_id === row.request.file_id
                    && event.expected_pub === row.request.expected_pub) {
                    row.finish(event.supported && event.peer_pub === row.request.expected_pub ? event : null);
                }
                return true;
            }
            if (event?.type === 'native_edit') { await receiver.receive(event); return true; }
            if (event?.native_edit_v !== 1) return false;
            if (event.type === 'native_media_status') { await sender.status(event); return true; }
            if (event.type === 'native_media_committed') { await receiver.confirm(event); return true; }
            return event.type === 'native_media_header_committed';
        },
    };
}
