// Real UI caption actions, both authors, prepared-send crash and lost full ACK.
async function runNativeCaptionJourney({ sender, receiver, sourceGateway, targetGateway, openSender, openReceiver, online, fixtureQuery = 'native-durable-' }) {
    const locate = async (page, text) => page.evaluate(async text => {
        const history = await import('./dmhistory.js'), found = await history.dmHistorySearch(text);
        const row = found.find(row => row.message_id.startsWith('native:') && row.file);
        return row && { id: row.message_id, fileId: row.message_id.split(':').at(-1), filename: row.file.filename };
    }, text);
    const snapshot = async (page, fileId) => page.evaluate(async fileId => {
        const history = await import('./dmhistory.js');
        const { accountDbName } = await import('./auth.js');
        const rows = await new Promise((resolve, reject) => {
            const request = indexedDB.open(accountDbName('proxion-dm-history'), 2);
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                const db = request.result, tx = db.transaction('messages'), get = tx.objectStore('messages').getAll();
                get.onsuccess = () => { resolve(get.result.filter(row => row.message_id.endsWith(':' + fileId))); db.close(); };
                get.onerror = () => { reject(get.error); db.close(); };
            };
        });
        if (rows.length !== 1) return { count: rows.length };
        const row = await history.dmHistoryGet(rows[0].message_id), bytes = Uint8Array.from(atob(row.file.data_b64), c => c.charCodeAt(0));
        return { count: 1, id: row.message_id, content: row.content, sequence: row.native_edit_seq || 0,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(byte => byte.toString(16).padStart(2, '0')).join('') };
    }, fileId);
    const edit = async (page, id, text) => {
        await page.bringToFront();
        await page.evaluate(id => document.getElementById('msg-' + id)?.scrollIntoView({ block: 'center' }), id);
        await page.click(`[id="msg-${id}"]`, { button: 'right' });
        await page.waitForSelector('#ctx-edit', { visible: true }); await page.click('#ctx-edit');
        await page.waitForSelector('.edit-input', { visible: true }); await page.focus('.edit-input');
        await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
        await page.keyboard.type(text); await page.keyboard.press('Enter');
        await page.waitForFunction(() => !document.querySelector('.edit-input'), { timeout: 12000 });
    };
    const waitCaption = async (page, fileId, text, sequence) => page.waitForFunction(async ({ fileId, text, sequence }) => {
        const h = await import('./dmhistory.js'), rows = await h.dmHistorySearch(text);
        const row = rows.find(row => row.message_id.endsWith(':' + fileId));
        return row && (await h.dmHistoryGet(row.message_id)).native_edit_seq === sequence;
    }, { timeout: 30000 }, { fileId, text, sequence }).catch(async error => {
        for (const [label, browser] of [['source', sender], ['target', receiver]]) {
            const state = await browser.evaluate(async fileId => {
                const e = await import('./e2e.js'), jobs = (await e.listDmSendJobs()).filter(row => row.kind === 'native-edit');
                const receives = await e.pendingDmReceives({ transport: 'native-edit' });
                return { jobs: jobs.map(row => ({ id: row.id, peer: row.peer, context: row.context, prepared: !!row.payload,
                    headerCommitted: !!row.headerCommitted, committed: !!row.committed, historySaved: !!row.historySaved })),
                    receives: receives.map(row => ({ id: row.receiveId, peer: row.peerId, event: row.data.event?.message_id })),
                    captions: (await (await import('./dmhistory.js')).dmHistorySearch('native-caption')).map(row => ({ id: row.message_id, text: row.content })), fileId };
            }, fileId);
            console.error('caption ' + label + ' diagnostic: ' + JSON.stringify(state));
            console.error((browser._console || []).slice(-12).join('\n'));
        }
        throw Error('caption ' + text + ' sequence ' + sequence + ': ' + error.message);
    });
    const first = await locate(sender, fixtureQuery);
    if (!first) throw Error('caption test cannot find the completed native inline original');
    const original = await snapshot(sender, first.fileId);
    const captionA = 'native-caption-A-' + Date.now();
    await openSender(); await openReceiver(); await edit(sender, first.id, captionA);
    await receiver.bringToFront(); await waitCaption(receiver, first.fileId, captionA, 1);
    await sender.bringToFront(); await waitCaption(sender, first.fileId, captionA, 1);
    for (const page of [sender, receiver]) { await page.reload({ waitUntil: 'load' }); await online(page); }
    await openSender(); await openReceiver();
    for (const page of [sender, receiver]) {
        const saved = await snapshot(page, first.fileId);
        if (saved.count !== 1 || saved.hash !== original.hash || saved.content !== captionA || saved.sequence !== 1) throw Error('caption A reload changed file/history: ' + JSON.stringify(saved));
    }
    const remoteId = (await snapshot(receiver, first.fileId)).id;
    await receiver.click(`[id="msg-${remoteId}"]`, { button: 'right' });
    if (await receiver.$eval('#ctx-edit', button => !!button.getClientRects().length)) throw Error('non-author was offered an edit of the peer attachment');
    await receiver.keyboard.press('Escape');
    const nameB = 'native-caption-owner-b-' + Date.now() + '.txt';
    await receiver.bringToFront();
    await receiver.evaluate(name => {
        const transfer = new DataTransfer(); transfer.items.add(new File(['B original bytes'], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
    }, nameB);
    await receiver.waitForSelector('#file-confirm-modal', { visible: true }); await receiver.click('#file-confirm-ok');
    await receiver.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).some(row => row.file), { timeout: 30000 }, nameB);
    await sender.bringToFront(); await sender.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).some(row => row.file), { timeout: 30000 }, nameB);
    const second = await locate(receiver, nameB), originalB = await snapshot(receiver, second.fileId), captionB = 'native-caption-B-' + Date.now();
    await edit(receiver, second.id, captionB); await sender.bringToFront(); await waitCaption(sender, second.fileId, captionB, 1);
    await receiver.bringToFront(); await waitCaption(receiver, second.fileId, captionB, 1);

    const failPrepared = () => {
        const set = Storage.prototype.setItem;
        window.__captionPreparationFailures = 0;
        Storage.prototype.setItem = function (key, value) {
            if (key.startsWith('proxion_e2e_send_job_ne-') && this.getItem(key) && !window.__allowCaptionSave) {
                window.__captionPreparationFailures++; throw new DOMException('caption prepared save interruption', 'QuotaExceededError');
            }
            return set.call(this, key, value);
        };
    };
    const holdHistoryAndCommit = () => {
        const put = IDBObjectStore.prototype.put, send = WebSocket.prototype.send;
        window.__captionHistoryFailures = 0; window.__captionLostCommits = 0;
        IDBObjectStore.prototype.put = function (row, ...args) {
            if (this.name === 'messages' && row.native_edit_seq === 2 && !window.__allowCaptionHistory) {
                window.__captionHistoryFailures++; this.transaction.abort(); return;
            }
            return put.call(this, row, ...args);
        };
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* another frame */ }
            if (packet?.cmd === 'native_media_commit' && packet.native_edit_v === 1) { window.__captionLostCommits++; return; }
            return send.call(this, wire);
        };
    };
    const sourceHook = await sender.evaluateOnNewDocument(failPrepared), targetHook = await receiver.evaluateOnNewDocument(holdHistoryAndCommit);
    await sender.reload({ waitUntil: 'load' }); await receiver.reload({ waitUntil: 'load' });
    await online(sender); await online(receiver); await openSender(); await openReceiver();
    const captionA2 = captionA + '-retried'; await edit(sender, first.id, captionA2);
    await sender.waitForFunction(() => window.__captionPreparationFailures > 0, { timeout: 20000 });
    const prepared = await sender.evaluate(async () => {
        const e = await import('./e2e.js'), job = (await e.listDmSendJobs()).find(row => row.kind === 'native-edit');
        return { id: job.id, sendId: job.sendId, header: (await e.loadRatchetState(job.peer)).pendingSends[job.sendId] };
    });
    if (!prepared.header || sourceGateway.nativeParts(prepared.id).transport) throw Error('caption interruption missed the pre-transmission ratchet journal');
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await sender.waitForFunction(() => window.__captionPreparationFailures > 0, { timeout: 15000 });
    const reloaded = await sender.evaluate(async () => {
        const e = await import('./e2e.js'), job = (await e.listDmSendJobs()).find(row => row.kind === 'native-edit');
        return { id: job.id, sendId: job.sendId, header: (await e.loadRatchetState(job.peer)).pendingSends[job.sendId] };
    });
    if (JSON.stringify(reloaded) !== JSON.stringify(prepared)) throw Error('caption reload replaced exact action/header');
    await sender.evaluate(() => { window.__allowCaptionSave = true; document.getElementById('settings-btn').click(); });
    await sender.evaluate(() => document.querySelector('[data-settings-target="settings-sec-pending"]').click());
    const pendingId = 'native-edit:' + prepared.id;
    await sender.waitForSelector(`[data-outbox-id="${pendingId}"]`, { timeout: 10000 });
    if (await sender.evaluate(id => !!document.querySelector(`[data-outbox-id="${id}"]`).closest('li').querySelector('[data-outbox-action="cancel"]'), pendingId)) throw Error('caption prepared action offered cancellation');
    await sender.focus(`[data-outbox-id="${pendingId}"][data-outbox-action="retry"]`); await sender.keyboard.press('Enter');
    await sender.evaluate(() => document.getElementById('settings-cancel-btn').click());
    await receiver.bringToFront(); await receiver.waitForFunction(() => window.__captionHistoryFailures > 0, { timeout: 20000 });
    const retained = targetGateway.nativeParts(prepared.id);
    if (!retained.header_committed || retained.status === 'committed') throw Error('failed caption history implied full consumption');
    await receiver.evaluate(() => { window.__allowCaptionHistory = true; });
    await waitCaption(receiver, first.fileId, captionA2, 2);
    await receiver.waitForFunction(() => window.__captionLostCommits > 0, { timeout: 15000 });
    await sourceGateway.restart(); await targetGateway.restart();
    await sender.removeScriptToEvaluateOnNewDocument(sourceHook.identifier); await receiver.removeScriptToEvaluateOnNewDocument(targetHook.identifier);
    await sender.reload({ waitUntil: 'load' }); await receiver.reload({ waitUntil: 'load' });
    await online(sender); await online(receiver); await openSender(); await openReceiver();
    await sender.bringToFront(); await waitCaption(sender, first.fileId, captionA2, 2);
    await sender.waitForFunction(async () => !(await (await import('./e2e.js')).listDmSendJobs()).some(row => row.kind === 'native-edit'), { timeout: 30000 });
    for (const page of [sender, receiver]) {
        const a = await snapshot(page, first.fileId), b = await snapshot(page, second.fileId);
        if (a.count !== 1 || a.hash !== original.hash || a.sequence !== 2 || a.content !== captionA2
            || b.count !== 1 || b.hash !== originalB.hash || b.sequence !== 1 || b.content !== captionB) throw Error('caption recovery changed attachment bytes or duplicated an original');
    }
    const later = 'text-after-native-captions-' + Date.now();
    await sender.bringToFront(); await sender.focus('#message-input'); await sender.type('#message-input', later); await sender.keyboard.press('Enter');
    await receiver.bringToFront(); await receiver.waitForFunction(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).length === 1, { timeout: 20000 }, later);
    console.log('  ✓ both primary authors edit native captions; non-author blocked, exact prepared edit survives reload, failed history/lost full ACK survive both gateway restarts, original hashes and later text preserved');
}

export async function nativeCaptionJourney(options) {
    const sessions = [], frames = [];
    for (const [label, page] of [['source', options.sender], ['target', options.receiver]]) {
        const session = await page.target().createCDPSession(); sessions.push(session); await session.send('Network.enable');
        const capture = direction => packet => {
            let data; try { data = JSON.parse(packet.response.payloadData); } catch { return; }
            if (['native_media_capability', 'native_edit_capability', 'native_media_capability_result', 'native_edit_capability_result'].includes(data.cmd || data.type)) {
                frames.push({ label, direction, ...data }); if (frames.length > 40) frames.shift();
            }
        };
        session.on('Network.webSocketFrameSent', capture('send')); session.on('Network.webSocketFrameReceived', capture('receive'));
    }
    try { return await runNativeCaptionJourney(options); }
    catch (error) {
        console.error('caption capability frames: ' + JSON.stringify(frames));
        for (const [label, page] of [['source', options.sender], ['target', options.receiver]]) {
            console.error('caption ' + label + ' pending: ' + JSON.stringify(await page.evaluate(async () => {
                const e = await import('./e2e.js'), did = localStorage.getItem('proxion_identity_did');
                const drafts = await (await import('./native-media-drafts.js')).createNativeMediaStore().list(
                    JSON.stringify(['outgoing', did, did, e.e2eScopedKey('')]));
                return { files: drafts.map(row => ({ id: row.id, stage: row.stage, name: row.metadata.name,
                    header: !!row.headerCommitted, full: !!row.committed, peer: row.peer, expectedPub: e.cachedPeerPub(row.peer) })),
                    edits: (await e.listDmSendJobs()).filter(row => row.kind === 'native-edit').map(row => ({ id: row.id,
                        prepared: !!row.payload, header: !!row.headerCommitted, full: !!row.committed })) };
            })));
            console.error((page._console || []).slice(-15).join('\n'));
        }
        console.error('caption gateway receipts: ' + JSON.stringify({ source: options.sourceGateway.nativeState(), target: options.targetGateway.nativeState() }));
        throw error;
    } finally { for (const session of sessions) await session.detach().catch(() => {}); }
}
