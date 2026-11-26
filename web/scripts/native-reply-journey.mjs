export async function nativeReplyJourney({ sender, receiver, linked, sourceGateway, openSender, openReceiver, online }) {
    const findPeerFile = async page => page.evaluate(async () => {
        const { accountDbName } = await import('./auth.js'), self = localStorage.getItem('proxion_identity_did');
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(accountDbName('proxion-dm-history'), 3);
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                const db = request.result, get = db.transaction('messages').objectStore('messages').getAll();
                get.onsuccess = () => { const row = get.result.filter(row => row.message_id.startsWith('native:') && row.from_webid !== self && row.file)
                    .sort((a, b) => b.timestamp.localeCompare(a.timestamp))[0]; db.close(); resolve(row && { id: row.message_id, fileId: row.message_id.split(':').at(-1) }); };
            };
        });
    });
    const sendReply = async (page, original, text) => {
        await page.bringToFront();
        await page.evaluate(id => document.getElementById('msg-' + id)?.scrollIntoView({ block: 'center' }), original.id);
        await page.click(`[id="msg-${original.id}"]`, { button: 'right' }); await page.waitForSelector('#ctx-reply', { visible: true }); await page.click('#ctx-reply');
        await page.focus('#message-input'); await page.type('#message-input', text); await page.keyboard.press('Enter');
    };
    const waitText = async (page, text) => page.waitForFunction(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).some(row => row.content === text), { timeout: 45000 }, text);
    const checkReply = async (page, text, fileId) => {
        const saved = await page.evaluate(async text => {
            const history = await import('./dmhistory.js'), rows = await history.dmHistorySearch(text);
            return rows.filter(row => row.content === text).map(row => ({ id: row.message_id, reply: row.reply_to_id,
                nativeRef: row.native_reply_ref, unavailable: row.native_reply_unavailable }));
        }, text);
        if (saved.length !== 1 || !saved[0].reply?.endsWith(':' + fileId) || saved[0].unavailable || !saved[0].nativeRef) throw Error('native reply lost its exact quote: ' + JSON.stringify(saved));
        return saved[0];
    };
    await openSender(); await openReceiver();
    const original = await findPeerFile(sender); if (!original) throw Error('native reply fixture has no peer attachment');
    const linkedId = await linked.evaluate(() => localStorage.getItem('proxion_identity_did'));
    const owner = await sender.evaluate(() => localStorage.getItem('proxion_identity_did'));
    const legacy = await linked.evaluateOnNewDocument(() => {
        const send = WebSocket.prototype.send;
        WebSocket.prototype.send = function (wire) {
            let data; try { data = JSON.parse(wire); } catch { /* unrelated */ }
            if (data?.cmd === 'register') return send.call(this, JSON.stringify({ ...data, native_ref_v: 0 }));
            return send.call(this, wire);
        };
    });
    await linked.reload({ waitUntil: 'load' }); await online(linked);
    const first = 'native-reply-with-held-mirror-' + Date.now();
    await sendReply(sender, original, first); await receiver.bringToFront(); await waitText(receiver, first);
    const received = await checkReply(receiver, first, original.fileId);
    await sender.bringToFront();
    await sender.waitForFunction(async text => (await (await import('./e2e.js')).listDmSendJobs()).some(job => job.record?.content === text && job.peerConfirmed), { timeout: 45000 }, first);
    const before = await sender.evaluate(async text => {
        const job = (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.record?.content === text);
        return { id: job.id, sendId: job.sendId, frames: job.frames };
    }, first);
    if (!before || !sourceGateway.queued(owner, linkedId)) throw Error('legacy mirror did not retain typed custody');
    const noLegacyCopy = await linked.evaluate(async text => !(await (await import('./dmhistory.js')).dmHistorySearch(text)).some(row => row.content === text), first);
    if (!noLegacyCopy) throw Error('a legacy-capability reader consumed a native reference frame');
    const later = 'text-while-native-mirror-held-' + Date.now();
    await sender.focus('#message-input'); await sender.type('#message-input', later); await sender.keyboard.press('Enter');
    await receiver.bringToFront(); await waitText(receiver, later);
    await sourceGateway.restart();
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    const after = await sender.evaluate(async text => {
        const job = (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.record?.content === text);
        return { id: job.id, sendId: job.sendId, frames: job.frames };
    }, first);
    if (JSON.stringify(before) !== JSON.stringify(after)) throw Error('reference mirror retry replaced frozen frames or send identity');
    await linked.removeScriptToEvaluateOnNewDocument(legacy.identifier); await linked.reload({ waitUntil: 'load' }); await online(linked);
    await linked.bringToFront(); await waitText(linked, first); await waitText(linked, later);
    const mirror = await linked.evaluate(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).filter(row => row.content === text), first);
    if (mirror.length !== 1 || !mirror[0].native_reply_ref || !mirror[0].native_reply_unavailable || mirror[0].file) throw Error('mirror did not keep readable text with an explicit unavailable quote');
    await sender.bringToFront();
    await sender.waitForFunction(async text => !(await (await import('./e2e.js')).listDmSendJobs()).some(job => job.record?.content === text), { timeout: 45000 }, first);
    for (const page of [sender, receiver]) { await page.reload({ waitUntil: 'load' }); await online(page); }
    await openSender(); await openReceiver();
    for (const page of [sender, receiver]) await checkReply(page, first, original.fileId);
    await receiver.bringToFront();
    await receiver.click(`[id="msg-${received.id}"] .reply-context`);
    await receiver.waitForFunction(id => document.getElementById('msg-' + id)?.getClientRects().length > 0, { timeout: 10000 }, received.reply);
    const reverseOriginal = await findPeerFile(receiver), reverse = 'native-reply-reverse-' + Date.now();
    await sendReply(receiver, reverseOriginal, reverse); await sender.bringToFront(); await waitText(sender, reverse);
    await checkReply(sender, reverse, reverseOriginal.fileId);
    await receiver.bringToFront(); await waitText(receiver, reverse); await checkReply(receiver, reverse, reverseOriginal.fileId);
    console.log('  ✓ native replies preserve both authors’ quotes and jump targets; a legacy mirror retains exact frames across restart, upgraded mirror keeps readable text without phantom files, and later text progresses independently');
}
