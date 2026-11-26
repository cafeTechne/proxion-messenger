export async function nativeActionJourney({ sender, receiver, linked, sourceGateway, targetGateway, openSender, openReceiver, openLinked, online }) {
    const idle = async page => { await page.bringToFront(); await page.waitForFunction(async () => (await (await import('./e2e.js')).listDmSendJobs()).length === 0, { timeout: 60000 }); };
    const find = async (page, own) => page.evaluate(async own => {
        const history = await import('./dmhistory.js'), self = localStorage.getItem('proxion_identity_did');
        const rows = await history.dmHistorySearch('native-');
        const row = rows.filter(row => row.file && row.message_id.startsWith('native:') && !String(row.file.mime_type).startsWith('audio/') && (row.from_webid === self) === own)
            .sort((a, b) => a.file.size - b.file.size).at(-1);
        return row ? { id: row.message_id, uuid: row.message_id.split(':').at(-1) } : null;
    }, own);
    const corresponding = async (page, uuid) => page.evaluate(async uuid => (await (await import('./dmhistory.js')).dmHistorySearch('native-')).find(row => row.file && row.message_id.endsWith(':' + uuid))?.message_id, uuid);
    const action = async (page, id, selector, confirm = false) => {
        await page.bringToFront(); await page.waitForSelector(`[id="msg-${id}"]`, { visible: true, timeout: 15000 });
        await page.evaluate(id => document.getElementById('msg-' + id)?.scrollIntoView({ block: 'center' }), id);
        await page.click(`[id="msg-${id}"]`, { button: 'right' }); await page.waitForSelector(selector, { visible: true }); await page.click(selector);
        if (confirm) { await page.waitForSelector('#confirm-ok', { visible: true }); await page.click('#confirm-ok'); }
    };
    const showPins = async page => { await page.bringToFront(); await page.click('#pin-panel-btn'); await page.waitForSelector('#pin-panel', { visible: true }); };
    const pinRow = id => `[data-pin-action="unpin"][data-msg-id="${id}"]`;
    const pinned = async (page, id, present = true) => {
        await page.bringToFront();
        if (await page.$eval('#pin-panel', panel => getComputedStyle(panel).display === 'none')) await showPins(page);
        await page.waitForFunction((id, present) => !!document.querySelector(`[data-pin-action="unpin"][data-msg-id="${id}"]`) === present,
            { timeout: 45000 }, id, present).catch(async error => {
                console.error('pin wait diagnostic:', JSON.stringify({ id, present, stored: await snapshot(page, id),
                    ui: await page.evaluate(async () => ({ pins: document.getElementById('pin-list')?.innerText,
                        toasts: [...document.querySelectorAll('.toast')].map(el => el.innerText),
                        jobs: (await (await import('./e2e.js')).listDmSendJobs()).map(job => ({ id: job.id, kind: job.nativeControl, prepared: !!job.frames, peerConfirmed: job.peerConfirmed })) })) }));
                throw Error('pin ' + id + ' present=' + present + ': ' + error.message);
            });
    };
    const deleted = async (page, id) => {
        await page.bringToFront(); await page.waitForFunction(async id => !(await (await import('./dmhistory.js')).dmHistoryGet(id))
            && !document.getElementById('msg-' + id), { timeout: 45000 }, id);
    };
    const closePins = async page => page.evaluate(() => document.getElementById('pin-panel-close')?.click());
    const snapshot = async (page, id) => page.evaluate(async id => {
        const { accountDbName } = await import('./auth.js');
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(accountDbName('proxion-dm-history'), 3);
            request.onsuccess = () => {
                const db = request.result, tx = db.transaction(['messages', 'native_files', 'native_state']);
                const message = tx.objectStore('messages').get(id), blob = tx.objectStore('native_files').get(id), state = tx.objectStore('native_state').get(id);
                tx.oncomplete = () => { db.close(); resolve({ message: !!message.result, blob: !!blob.result, state: state.result }); };
                tx.onabort = tx.onerror = () => reject(tx.error);
            }; request.onerror = () => reject(request.error);
        });
    }, id);
    const sessions = [], wire = [];
    for (const [label, page] of [['sender', sender], ['receiver', receiver], ['linked', linked]]) {
        const session = await page.createCDPSession(); sessions.push(session); await session.send('Network.enable');
        for (const [event, direction] of [['Network.webSocketFrameSent', 'out'], ['Network.webSocketFrameReceived', 'in']]) session.on(event, packet => {
            try { const data = JSON.parse(packet.response.payloadData); wire.push({ label, direction, cmd: data.cmd, type: data.type,
                mid: data.message_id, thread: data.thread_id, ref: data.native_ref_v, action: data.native_action_v, supported: data.supported,
                error: data.message }); if (wire.length > 80) wire.shift(); } catch { /* unrelated frame */ }
        });
    }
    try {
    await idle(sender); await idle(receiver); await openSender(); await openReceiver();
    const a = await find(sender, true), b = await find(receiver, true);
    if (!a || !b) throw Error('native delete/pin fixtures missing');
    const aAtB = await corresponding(receiver, a.uuid), bAtA = await corresponding(sender, b.uuid);
    const replayA = await sender.evaluate(async id => (await import('./dmhistory.js')).dmHistoryGet(id), a.id);
    const replayAtB = await receiver.evaluate(async id => (await import('./dmhistory.js')).dmHistoryGet(id), aAtB);
    console.log('  · native delete/pin fixtures located; starting ordinary/native pin coexistence');
    if (!(await snapshot(sender, a.id)).blob || !(await snapshot(receiver, aAtB)).blob) throw Error('native deletion fixture must include actual Blob custody');
    const legacy = await linked.evaluateOnNewDocument(() => {
        const send = WebSocket.prototype.send;
        WebSocket.prototype.send = function (wire) {
            let data; try { data = JSON.parse(wire); } catch { /* unrelated */ }
            if (data?.cmd === 'register') return send.call(this, JSON.stringify({ ...data, native_action_v: 0 }));
            return send.call(this, wire);
        };
    });
    await linked.reload({ waitUntil: 'load' }); await online(linked);
    const ordinary = await sender.evaluate(async () => (await (await import('./dmhistory.js')).dmHistorySearch('text-after-native-reaction-')).find(row => !row.file)?.message_id);
    if (!ordinary) throw Error('ordinary pin fixture missing');
    await action(sender, ordinary, '#ctx-pin'); await showPins(sender); await pinned(sender, ordinary); await closePins(sender);
    await action(sender, bAtA, '#ctx-pin'); await showPins(sender); await pinned(sender, bAtA); await pinned(sender, ordinary);
    await showPins(receiver); await pinned(receiver, b.id);
    await sender.bringToFront(); await sender.waitForFunction(async () => (await (await import('./e2e.js')).listDmSendJobs()).some(job => job.nativeControl === 'action' && job.peerConfirmed), { timeout: 45000 });
    const frozen = await sender.evaluate(async () => { const job = (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.nativeControl === 'action'); return { id: job.id, sendId: job.sendId, frames: job.frames }; });
    await sourceGateway.restart(); await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    const retained = await sender.evaluate(async () => { const job = (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.nativeControl === 'action'); return { id: job.id, sendId: job.sendId, frames: job.frames }; });
    if (JSON.stringify(frozen) !== JSON.stringify(retained)) throw Error('reply-only mirror pin retry replaced frozen action');
    await linked.removeScriptToEvaluateOnNewDocument(legacy.identifier); await linked.reload({ waitUntil: 'load' }); await online(linked); await openLinked();
    await showPins(linked); await pinned(linked, bAtA);
    if (!await linked.$eval('#pin-list', list => list.innerText.includes('not available') || list.innerText.includes('unavailable'))) {
        // Check the actual absence of a file as well; translated copy can vary.
        if (await linked.evaluate(async id => !!(await (await import('./dmhistory.js')).dmHistoryGet(id))?.file, bAtA)) throw Error('linked pin preview fabricated an original file');
    }
    await idle(sender);
    // A linked reader may remove a retained pin without inventing its missing
    // original. It uses the same signed original-reference preflight.
    await linked.bringToFront(); await linked.click(pinRow(bAtA)); await pinned(linked, bAtA, false);
    await showPins(sender); await pinned(sender, bAtA, false); await receiver.bringToFront(); await pinned(receiver, b.id, false); await idle(linked);
    await sender.bringToFront(); await sender.click(pinRow(ordinary)); await pinned(sender, ordinary, false);
    console.log('  · native pins coexist with ordinary text pins and survive action-legacy mirror restart/upgrade; linked unavailable preview unpins through the retained reference');
    await closePins(receiver); await openReceiver(); await action(receiver, aAtB, '#ctx-pin');
    await showPins(receiver); await pinned(receiver, aAtB); await sender.bringToFront(); await pinned(sender, a.id); await idle(receiver);
    await sender.bringToFront(); await sender.click(pinRow(a.id)); await pinned(sender, a.id, false); await receiver.bringToFront(); await pinned(receiver, aAtB, false); await idle(sender);
    await closePins(sender); await openSender(); await action(sender, bAtA, '#ctx-pin'); await idle(sender);
    for (const page of [sender, receiver]) { await page.reload({ waitUntil: 'load' }); await online(page); }
    await openSender(); await openReceiver(); await showPins(sender); await showPins(receiver); await pinned(sender, bAtA); await pinned(receiver, b.id);
    // Read the native preview offline, then navigate to the real retained file.
    await sender.setOfflineMode(true); await sender.bringToFront(); await closePins(sender); await showPins(sender); await pinned(sender, bAtA);
    await sender.click(`[data-pin-action="jump"][data-msg-id="${bAtA}"]`);
    await sender.waitForFunction(id => !!document.getElementById('msg-' + id)?.getClientRects().length, { timeout: 10000 }, bAtA);
    await sender.setOfflineMode(false); await online(sender);
    await receiver.bringToFront(); await receiver.click(pinRow(b.id)); await pinned(receiver, b.id, false); await sender.bringToFront(); await pinned(sender, bAtA, false); await idle(receiver);
    console.log('  · both primary authors pin/unpin and reload/offline previews passed');
    await closePins(receiver); await openReceiver();
    await receiver.waitForSelector(`[id="msg-${aAtB}"]`, { visible: true, timeout: 15000 });
    await receiver.click(`[id="msg-${aAtB}"]`, { button: 'right' });
    if (await receiver.$eval('#ctx-delete', button => !!button.getClientRects().length)) throw Error('non-author offered delete-for-everyone');
    await receiver.keyboard.press('Escape'); await action(receiver, aAtB, '#ctx-delete-me'); await deleted(receiver, aAtB);
    if (!(await snapshot(sender, a.id)).message || !(await snapshot(sender, a.id)).blob) throw Error('delete-for-me removed the peer copy');
    if ((await snapshot(receiver, aAtB)).blob || !(await snapshot(receiver, aAtB)).state?.deletedLocal) throw Error('delete-for-me did not atomically retain local evidence and remove Blob');
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver(); await deleted(receiver, aAtB);
    console.log('  · local-only deletion retained the peer Blob and survived reload');
    // Freeze a caption header before deleting its original. Releasing the
    // failed prepared save afterwards must finish that exact action safely.
    const heldCaption = await sender.evaluateOnNewDocument(() => {
        const set = Storage.prototype.setItem; window.__deleteCaptionFailures = 0;
        Storage.prototype.setItem = function (key, value) {
            if (key.startsWith('proxion_e2e_send_job_ne-') && this.getItem(key) && !window.__allowDeletedCaption) {
                window.__deleteCaptionFailures++; throw new DOMException('prepared caption interruption', 'QuotaExceededError');
            } return set.call(this, key, value);
        };
    });
    const lostDelete = await receiver.evaluateOnNewDocument(() => {
        const send = WebSocket.prototype.send;
        WebSocket.prototype.send = function (wire) {
            let data; try { data = JSON.parse(wire); } catch { /* unrelated */ }
            if (data?.cmd === 'dm_receive_commit' && data.native_action_v === 1) return;
            return send.call(this, wire);
        };
    });
    await sender.reload({ waitUntil: 'load' }); await receiver.reload({ waitUntil: 'load' }); await online(sender); await online(receiver); await openSender(); await openReceiver();
    await action(sender, a.id, '#ctx-edit'); await sender.waitForSelector('.edit-input', { visible: true });
    await sender.focus('.edit-input'); await sender.keyboard.down('Control'); await sender.keyboard.press('A'); await sender.keyboard.up('Control');
    await sender.keyboard.type('caption-after-original-deleted-' + Date.now()); await sender.keyboard.press('Enter');
    await sender.waitForFunction(() => window.__deleteCaptionFailures > 0, { timeout: 20000 });
    const caption = await sender.evaluate(async () => { const e = await import('./e2e.js'), job = (await e.listDmSendJobs()).find(job => job.kind === 'native-edit'); return { id: job.id, sendId: job.sendId, header: (await e.loadRatchetState(job.peer)).pendingSends[job.sendId] }; });
    await action(sender, a.id, '#ctx-delete', true); await deleted(sender, a.id); await deleted(receiver, aAtB);
    await sender.bringToFront(); await sender.waitForFunction(async () => (await (await import('./e2e.js')).listDmSendJobs()).some(job => job.nativeControl === 'action' && job.record.native_action.op === 'delete' && job.frames?.flatMap(frame => frame.fanout || []).length === job.entries.length), { timeout: 20000 });
    const deleteBefore = await sender.evaluate(async () => { const job = (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.nativeControl === 'action'); return { id: job.id, sendId: job.sendId, frames: job.frames }; });
    await sourceGateway.restart(); await targetGateway.restart(); await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    const deleteAfter = await sender.evaluate(async () => { const job = (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.nativeControl === 'action'); return { id: job.id, sendId: job.sendId, frames: job.frames }; });
    if (JSON.stringify(deleteBefore) !== JSON.stringify(deleteAfter)) throw Error('deleted source lost its exact prepared action after restart');
    const resumedCaption = await sender.evaluate(async () => { const e = await import('./e2e.js'), job = (await e.listDmSendJobs()).find(job => job.kind === 'native-edit'); return { id: job.id, sendId: job.sendId, header: (await e.loadRatchetState(job.peer)).pendingSends[job.sendId] }; });
    if (JSON.stringify(caption) !== JSON.stringify(resumedCaption)) throw Error('deletion replaced the pending caption header');
    await sender.removeScriptToEvaluateOnNewDocument(heldCaption.identifier); await receiver.removeScriptToEvaluateOnNewDocument(lostDelete.identifier);
    await sender.reload({ waitUntil: 'load' }); await receiver.reload({ waitUntil: 'load' }); await online(sender); await online(receiver); await openSender(); await openReceiver(); await idle(sender);
    for (const [page, id] of [[sender, a.id], [receiver, aAtB]]) {
        const saved = await snapshot(page, id); if (saved.message || saved.blob || !saved.state?.deletedEveryone) throw Error('deleted file/Blob returned after recovery');
        await page.evaluate(async ({ original, id }) => {
            const row = { ...original };
            // Real persistence entry points, with the original retained bytes.
            const history = await import('./dmhistory.js'); await history.dmHistorySave(row, { requireFile: true });
            const bytes = Uint8Array.from(atob(row.file.data_b64), c => c.charCodeAt(0));
            await history.dmHistorySaveFile(row, new Blob([bytes]));
        }, { original: page === sender ? replayA : replayAtB, id });
        if ((await snapshot(page, id)).message || (await snapshot(page, id)).blob) throw Error('delayed original replay resurrected deleted bytes');
    }
    await action(receiver, b.id, '#ctx-delete', true); await deleted(receiver, b.id); await deleted(sender, bAtA); await idle(receiver);
    const later = 'text-after-native-delete-' + Date.now(); await sender.bringToFront(); await sender.focus('#message-input'); await sender.type('#message-input', later); await sender.keyboard.press('Enter');
    await receiver.bringToFront(); await receiver.waitForFunction(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).some(row => row.content === text), { timeout: 45000 }, later);
    console.log('  ✓ native pin/unpin works for both participants, ordinary pins remain separate, previews reload/offline; local-only and author-only deletion remove Blob/history atomically, survive both gateway restarts, retain exact caption/delete headers, reject delayed replay, and preserve later text');
    } catch (error) { console.error('native action wire diagnostic:', JSON.stringify(wire)); throw error; }
    finally { for (const session of sessions) await session.detach().catch(() => {}); }
}
