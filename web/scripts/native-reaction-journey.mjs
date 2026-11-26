export async function nativeReactionJourney({ sender, receiver, linked, sourceGateway, openSender, openReceiver, online }) {
    const rows = async page => page.evaluate(async () => new Promise((resolve, reject) => {
        const request = indexedDB.open('proxion-native-reactions-v1', 1);
        request.onupgradeneeded = () => { const store = request.result.createObjectStore('actions', { keyPath: 'key' }); store.createIndex('scope', 'scope'); };
        request.onerror = () => reject(request.error);
        request.onsuccess = () => { const db = request.result, get = db.transaction('actions').objectStore('actions').getAll();
            get.onsuccess = () => { db.close(); resolve(get.result); }; get.onerror = () => reject(get.error); };
    }));
    const peerFile = async page => page.evaluate(async () => {
        const self = localStorage.getItem('proxion_identity_did');
        return (await (await import('./dmhistory.js')).dmHistorySearch('native-caption')).filter(row => row.file && row.from_webid !== self).at(-1)?.message_id;
    });
    const react = async (page, id) => {
        await page.bringToFront(); await page.evaluate(id => document.getElementById('msg-' + id)?.scrollIntoView({ block: 'center' }), id);
        await page.click(`[id="msg-${id}"]`, { button: 'right' }); await page.waitForSelector('#ctx-react', { visible: true }); await page.click('#ctx-react');
        await page.waitForSelector('#emoji-picker [data-emoji="👍"]', { visible: true }); await page.click('#emoji-picker [data-emoji="👍"]');
    };
    const pill = async (page, id, active = null) => {
        await page.bringToFront();
        await page.waitForFunction((id, active) => [...(document.getElementById('reactions-' + id)?.querySelectorAll('.reaction') || [])]
            .some(el => el.innerText.includes('👍') && (active === null || el.classList.contains('active') === active)), { timeout: 45000 }, id, active).catch(async error => {
                console.error('reaction pill diagnostic:', JSON.stringify({ id, active, rows: await rows(page),
                    page: await page.evaluate(async () => ({ toasts: [...document.querySelectorAll('.toast')].map(el => el.innerText),
                        jobs: (await (await import('./e2e.js')).listDmSendJobs()).map(job => ({ id: job.id, control: job.nativeControl, prepared: !!job.frames, peerConfirmed: job.peerConfirmed })) })) }));
                console.error((page._console || []).slice(-15).join('\n')); throw error;
            });
    };
    const noPill = async (page, id) => {
        await page.bringToFront(); await page.waitForFunction(id => ![...(document.getElementById('reactions-' + id)?.querySelectorAll('.reaction') || [])]
            .some(el => el.innerText.includes('👍')), { timeout: 45000 }, id);
    };
    const idle = async page => { await page.bringToFront(); await page.waitForFunction(async () => (await (await import('./e2e.js')).listDmSendJobs()).length === 0, { timeout: 60000 }); };
    await idle(sender); await idle(receiver); await openSender(); await openReceiver();
    console.log('  · native reaction source journals idle; starting add/remove recovery');
    const id = await peerFile(sender); if (!id) throw Error('reaction fixture missing peer file');
    const uuid = id.split(':').at(-1);
    const otherId = await receiver.evaluate(async uuid => (await (await import('./dmhistory.js')).dmHistorySearch('native-caption')).find(row => row.file && row.message_id.endsWith(':' + uuid))?.message_id, uuid);
    if (!otherId) throw Error('reaction fixture original missing at author');
    // Persist receiver state, but lose its full consumption proof. The source
    // must retain exact encrypted frames through restart until that proof retries.
    const loseAck = await receiver.evaluateOnNewDocument(() => {
        const send = WebSocket.prototype.send, put = IDBObjectStore.prototype.put;
        window.__reactionFailedWrites = 0;
        IDBObjectStore.prototype.put = function (row, ...args) {
            if (this.name === 'actions' && !window.__reactionFailedWrites) { window.__reactionFailedWrites++; this.transaction.abort(); return; }
            return put.call(this, row, ...args);
        };
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* other wire */ }
            if (packet?.cmd === 'dm_receive_commit' && packet.native_ref_v === 1) return;
            return send.call(this, wire);
        };
    });
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await react(sender, id); await pill(sender, id, true); await pill(receiver, otherId, false);
    console.log('  · native reaction visible on both primaries after injected receiver state failure');
    if (await receiver.evaluate(() => window.__reactionFailedWrites) !== 1) throw Error('reaction failure injection did not run');
    await sender.bringToFront();
    await sender.waitForFunction(async () => (await (await import('./e2e.js')).listDmSendJobs()).some(job => job.nativeControl === 'reaction'
        && job.frames?.flatMap(frame => frame.fanout || []).length === job.entries.length), { timeout: 20000 });
    const before = await sender.evaluate(async () => {
        const job = (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.nativeControl === 'reaction');
        return job && { id: job.id, sendId: job.sendId, frames: job.frames };
    });
    if (!before?.frames) throw Error('lost reaction receive ACK did not retain frozen source frames');
    await sourceGateway.restart(); await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender(); await pill(sender, id, true);
    const after = await sender.evaluate(async () => {
        const job = (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.nativeControl === 'reaction');
        return job && { id: job.id, sendId: job.sendId, frames: job.frames };
    });
    if (JSON.stringify(before) !== JSON.stringify(after)) throw Error('reaction restart replaced prepared ciphertext');
    await receiver.removeScriptToEvaluateOnNewDocument(loseAck.identifier); await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await pill(receiver, otherId, false); await idle(sender);
    console.log('  · native reaction lost FULL ACK recovered after source restart');
    const first = (await rows(sender)).find(row => row.target === id && row.op === 'add');
    if (!first) throw Error('reaction token not retained');
    await sender.bringToFront(); await sender.click(`[id="reactions-${id}"] .reaction.active`);
    await noPill(sender, id); await noPill(receiver, otherId); await idle(sender);
    await react(sender, id); await pill(sender, id, true); await pill(receiver, otherId, false); await idle(sender);
    const last = (await rows(sender)).find(row => row.target === id && row.op === 'add');
    if (last?.tok !== first.tok || last.seq !== 3) throw Error('remove/re-add replaced the original token or sequence');
    for (const page of [sender, receiver]) { await page.reload({ waitUntil: 'load' }); await online(page); }
    await openSender(); await openReceiver(); await pill(sender, id, true); await pill(receiver, otherId, false);
    const reverseId = await peerFile(receiver); if (!reverseId) throw Error('reverse reaction fixture missing');
    const reverseUuid = reverseId.split(':').at(-1);
    const reverseOther = await sender.evaluate(async uuid => (await (await import('./dmhistory.js')).dmHistorySearch('native-caption')).find(row => row.file && row.message_id.endsWith(':' + uuid))?.message_id, reverseUuid);
    await react(receiver, reverseId); await pill(receiver, reverseId, true); await pill(sender, reverseOther, false); await idle(receiver);
    await receiver.bringToFront(); await receiver.click(`[id="reactions-${reverseId}"] .reaction.active`);
    await noPill(receiver, reverseId); await noPill(sender, reverseOther); await idle(receiver);
    // Linked readers retain reaction state even without the original file.
    const linkedRows = await rows(linked);
    if (!linkedRows.some(row => row.tok === first.tok && row.seq === 3)) throw Error('linked reader lost delayed-original reaction metadata');
    const phantom = await linked.evaluate(async uuid => (await (await import('./dmhistory.js')).dmHistorySearch('native-caption')).some(row => row.file && row.message_id.endsWith(':' + uuid)), uuid);
    if (phantom) throw Error('reaction fabricated a linked-device attachment');
    const later = 'text-after-native-reaction-' + Date.now();
    await sender.bringToFront(); await sender.focus('#message-input'); await sender.type('#message-input', later); await sender.keyboard.press('Enter');
    await receiver.bringToFront(); await receiver.waitForFunction(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).some(row => row.content === text), { timeout: 45000 }, later);
    console.log('  ✓ native reaction add/remove/re-add retains its token through reload; failed receiver state and lost FULL ACK recover exact frames across gateway/browser restart; both authors and linked pending metadata work, with no phantom file and readable later text');
}
