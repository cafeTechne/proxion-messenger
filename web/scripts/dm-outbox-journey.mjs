// Crash after encryption but before prepared-frame persistence, then lose the
// real delivery acknowledgement and reload again. Every retry must reuse bytes.
export async function dmOutboxJourney({ sender, receiver, openSender, openReceiver, label }) {
    const text = label + '-durable-send-' + Date.now();
    await openSender(); await openReceiver();
    const bridge = await sender.evaluate(() => !!window.proxionCrossMode?.isReady());
    await receiver.evaluate(async () => {
        localStorage.setItem('dmHistoryEnabled', 'true');
        (await import('./dmhistory.js')).dmHistorySetEnabled(true);
    });
    // Failure before the intent commit must leave the composer intact and
    // must not create an encrypted job or spend a message key.
    const probe = text + '-unsaved-intent';
    await sender.evaluate(() => {
        const original = Storage.prototype.setItem;
        window.__restoreIntentWrites = () => { Storage.prototype.setItem = original; };
        Storage.prototype.setItem = function (key, value) {
            if (key.startsWith('proxion_e2e_send_job_')) throw new DOMException('intent interruption', 'QuotaExceededError');
            return original.call(this, key, value);
        };
    });
    try {
        await sender.focus('#message-input'); await sender.type('#message-input', probe); await sender.keyboard.press('Enter');
        await sender.waitForFunction(text => document.getElementById('message-input').value === text, { timeout: 10000 }, probe);
        const queued = await sender.evaluate(async text => (await (await import('./e2e.js')).listDmSendJobs()).some(job => job.record.content === text), probe);
        if (queued) throw Error('failed intent write left an unexpected send job');
    } finally {
        await sender.evaluate(() => {
            window.__restoreIntentWrites();
            const input = document.getElementById('message-input'); input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true }));
        });
    }
    async function reviewPending(id, { retry = false } = {}) {
        await sender.evaluate(() => document.getElementById('settings-btn').click());
        await sender.evaluate(() => document.querySelector('[data-settings-target="settings-sec-pending"]').click());
        await sender.waitForFunction(id => document.querySelector(`[data-outbox-id="${id}"]`), { timeout: 10000 }, id);
        const cancellable = await sender.evaluate(id => document.querySelector(`[data-outbox-id="${id}"]`).closest('li').querySelector('[data-outbox-action="cancel"]') !== null, id);
        if (cancellable) throw Error('prepared pending send incorrectly offers cancellation');
        if (retry) {
            await sender.focus(`[data-outbox-id="${id}"][data-outbox-action="retry"]`);
            await sender.keyboard.press('Enter');
        }
        await sender.evaluate(() => document.getElementById('settings-cancel-btn').click());
    }
    const wire = [];
    const collect = value => {
        if (!value || typeof value !== 'object') return;
        if (value.cmd === 'pod_fetch' && value.method === 'POST') { try { collect(JSON.parse(value.body)); } catch { /* non-DM */ } return; }
        if (value.message_id !== id) return;
        if (value.cmd === 'send_dm_fanout') wire.push(...value.fanout.map(entry => entry.payload));
        else if (value.e2e) wire.push(value);
    };
    let id = null;
    const requestListener = request => {
        if (request.method() !== 'POST') return;
        try { collect(JSON.parse(request.postData() || 'null')); } catch { /* form or other request */ }
    };
    sender.on('request', requestListener);
    const cdp = await sender.createCDPSession(); await cdp.send('Network.enable');
    cdp.on('Network.webSocketFrameSent', event => { try { collect(JSON.parse(event.response.payloadData)); } catch { /* other frame */ } });
    const blockPrepared = () => {
        const original = Storage.prototype.setItem;
        window.__outboxWrites = 0;
        Storage.prototype.setItem = function (key, value) {
            if (key.startsWith('proxion_e2e_send_job_') && !window.__releaseOutboxWrites) {
                window.__outboxWrites++;
                // The initial page allows its intent, then fails preparation.
                if (window.__blockEveryOutboxWrite || window.__outboxWrites > 1) {
                    throw new DOMException('prepared outbox interruption', 'QuotaExceededError');
                }
            }
            return original.call(this, key, value);
        };
    };
    await sender.evaluate(blockPrepared);
    await sender.focus('#message-input'); await sender.type('#message-input', text); await sender.keyboard.press('Enter');
    await sender.waitForFunction(async text => {
        const jobs = await (await import('./e2e.js')).listDmSendJobs();
        return jobs.some(job => job.record.content === text) && window.__outboxWrites > 1;
    }, { timeout: 20000 }, text);
    const before = await sender.evaluate(async text => {
        const e2e = await import('./e2e.js');
        const job = (await e2e.listDmSendJobs()).find(row => row.record.content === text);
        const entries = [];
        for (const entry of job.entries) {
            entries.push((await e2e.loadRatchetState(entry.peerId)).pendingSends[job.sendId]);
        }
        return { id: job.id, frames: !!job.frames, entries, timestamp: job.record.timestamp };
    }, text);
    id = before.id;
    await reviewPending(id);
    if (before.frames || before.entries.some(entry => !entry)) throw Error('outbox interruption missed the atomic recovery window');
    if (await receiver.evaluate(text => document.getElementById('message-feed').textContent.includes(text), text)) throw Error('a frame left before prepared outbox persistence');
    // Hold restored jobs until acknowledgement loss is installed on the new page.
    const blocker = await sender.evaluateOnNewDocument(blockPrepared);
    const every = await sender.evaluateOnNewDocument(() => { window.__blockEveryOutboxWrite = true; });
    const lostWs = await sender.evaluateOnNewDocument(id => {
        const original = WebSocket.prototype.send, wrapped = new WeakSet();
        WebSocket.prototype.send = function (data) {
            let frame; try { frame = JSON.parse(data); } catch { /* other data */ }
            if (frame?.message_id === id && ['send_dm', 'local_dm', 'send_dm_fanout'].includes(frame.cmd) && !wrapped.has(this)) {
                wrapped.add(this); const prior = this.onmessage;
                this.onmessage = function (event) {
                    let reply; try { reply = JSON.parse(event.data); } catch { /* other data */ }
                    if (reply?.message_id === id && ['message', 'send_dm_fanout_ack'].includes(reply.type)) return;
                    return prior?.call(this, event);
                };
            }
            return original.call(this, data);
        };
    }, id);
    try {
        await sender.reload({ waitUntil: 'networkidle2' });
        await sender.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 30000 });
        await openSender();
        if (bridge) await sender.waitForFunction(() => window.proxionCrossMode?.isReady(), { timeout: 30000 });
        await sender.evaluate(async id => {
            if (window.proxionCrossMode?.isReady()) {
                const io = window.proxionCrossMode.bridge.io, original = io.podDropDm.bind(io);
                io.podDropDm = async (root, env) => { const ok = await original(root, env); return env.message_id === id ? false : ok; };
            } else if (window.proxionWebDm) {
                const session = (await import('./auth.js')).solidSession, original = session.fetch.bind(session);
                session.fetch = async (url, options) => {
                    const response = await original(url, options);
                    let envelope; try { envelope = JSON.parse(options?.body || 'null'); } catch { /* other write */ }
                    return options?.method === 'POST' && envelope?.message_id === id
                        ? new Response('delivered but acknowledgement lost', { status: 503 }) : response;
                };
            }
            window.__releaseOutboxWrites = true;
        }, id);
        await reviewPending(id, { retry: true });
        for (let attempt = 0; attempt < 50; attempt++) {
            await receiver.evaluate(() => window.proxionWebDm?.drainOnce() || window.proxionCrossMode?.drainOnce());
            if (await receiver.evaluate(text => document.getElementById('message-feed').textContent.includes(text), text)) break;
            await new Promise(resolve => setTimeout(resolve, 400));
        }
        if (!await receiver.evaluate(text => document.getElementById('message-feed').textContent.includes(text), text)) throw Error('restored durable send never reached receiver');
        const pending = await sender.evaluate(async id => (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.id === id), id);
        if (!pending?.frames || pending.confirmed) throw Error('uncertain delivery was not retained as pending');
    } finally {
        await sender.removeScriptToEvaluateOnNewDocument(blocker.identifier);
        await sender.removeScriptToEvaluateOnNewDocument(every.identifier);
        await sender.removeScriptToEvaluateOnNewDocument(lostWs.identifier);
    }
    // The delivery already happened. This reload retries its exact payload and
    // receives confirmation; the recipient must retain one message.
    await sender.reload({ waitUntil: 'networkidle2' });
    await sender.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 30000 });
    await openSender();
    await sender.waitForFunction(async id => !(await (await import('./e2e.js')).listDmSendJobs()).some(job => job.id === id), { timeout: 40000 }, id);
    await receiver.evaluate(() => window.proxionWebDm?.drainOnce() || window.proxionCrossMode?.drainOnce());
    const rows = await receiver.evaluate(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).filter(row => row.content === text).length, text);
    if (rows !== 1) throw Error('durable retry did not retain exactly one received message: ' + rows);
    if (wire.length < before.entries.length * 2) throw Error('wire evidence did not include both delivery attempts');
    for (const payload of wire) {
        const exact = before.entries.find(entry => entry.nonce === payload.nonce);
        if (!exact || exact.ciphertext !== payload.content || exact.msgNum !== payload.msg_num
            || exact.ratchetPub !== payload.ratchet_pub || exact.pn !== payload.pn) throw Error('retry changed an encrypted payload');
        if (payload.timestamp && payload.timestamp !== before.timestamp) throw Error('retry changed the signed pod envelope timestamp');
    }
    sender.off('request', requestListener); await cdp.detach();
    console.log(`  ✓ ${label}: pending review and keyboard retry survive a prepared-write crash and lost acknowledgement with identical ciphertext and one received message`);
}
