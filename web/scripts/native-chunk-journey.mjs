// Integrated browser/gateway restart recovery with actual ciphertext and Blob
// history. Faults are installed before connection creation (the pacer binds send).
export async function nativeChunkJourney({ sender, receiver, sourceGateway, targetGateway, openSender, openReceiver, online, size }) {
    const name = 'native-resume-' + size + '-' + Date.now() + '.txt';
    const reader = () => {
        window.__chunkScope = async () => {
            const did = localStorage.getItem('proxion_identity_did'), e2e = await import('./e2e.js');
            return JSON.stringify(['outgoing', did, did, e2e.e2eScopedKey('')]);
        };
        window.__chunkDrafts = async () => (await import('./native-media-drafts.js')).createNativeMediaStore().list(await window.__chunkScope());
        window.__chunkInbox = async () => (await import('./native-media-inbox.js')).createNativeMediaInbox().list(await window.__chunkScope());
    };
    const holdUpload = () => {
        const send = WebSocket.prototype.send;
        window.__chunkUploadParts = [];
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* other frame */ }
            if (packet?.cmd === 'native_media_part') {
                window.__chunkUploadParts.push(packet.seq);
                if (packet.seq >= 3 && !window.__chunkAllowUpload) return;
            }
            return send.call(this, wire);
        };
    };
    const holdReceive = blocked => {
        const send = WebSocket.prototype.send, put = IDBObjectStore.prototype.put;
        let fetches = 0;
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* other frame */ }
            if (packet?.cmd === 'native_media_fetch' && !window.__chunkAllowDownload && ((blocked && !window.__chunkAllowFirstWindow) || ++fetches > 1)) return;
            if (packet?.cmd === 'native_media_commit' && !window.__chunkAllowCommit) {
                window.__chunkLostCommits = (window.__chunkLostCommits || 0) + 1; return;
            }
            return send.call(this, wire);
        };
        IDBObjectStore.prototype.put = function (...args) {
            if (this.name === 'native_files' && !window.__chunkAllowHistory) {
                window.__chunkHistoryFailures = (window.__chunkHistoryFailures || 0) + 1;
                this.transaction.abort(); return;
            }
            return put.apply(this, args);
        };
    };
    const sourceReader = await sender.evaluateOnNewDocument(reader), targetReader = await receiver.evaluateOnNewDocument(reader);
    const uploadHook = await sender.evaluateOnNewDocument(holdUpload);
    let receiveHook = await receiver.evaluateOnNewDocument(holdReceive, true);
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await sender.bringToFront();
    const expectedHash = await sender.evaluate(async ({ name, size }) => {
        const bytes = new Uint8Array(size);
        for (let i = 0; i < size; i++) bytes[i] = 65 + i % 26;
        const data = new DataTransfer(); data.items.add(new File([bytes], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = data.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
    }, { name, size });
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await sender.waitForFunction(() => window.__chunkUploadParts.includes(3), { timeout: 60000 });
    const before = await sender.evaluate(async name => {
        const row = (await window.__chunkDrafts()).find(row => row.metadata.name === name);
        return { id: row.id, header: row.hdr,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].map(value => value.toString(16).padStart(2, '0')).join('') };
    }, name);
    if (sourceGateway.nativeParts(before.id).parts !== 3) throw Error('upload pause did not leave three durable gateway parts');
    console.log(`  · native ${size}: partial source upload committed; restarting source`);
    await sourceGateway.restart();
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await sender.waitForFunction(() => window.__chunkUploadParts.includes(3), { timeout: 60000 });
    const resumed = await sender.evaluate(async id => {
        const row = (await window.__chunkDrafts()).find(row => row.id === id);
        return { header: row.hdr, parts: window.__chunkUploadParts,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].map(value => value.toString(16).padStart(2, '0')).join('') };
    }, before.id);
    if (resumed.hash !== before.hash || JSON.stringify(resumed.header) !== JSON.stringify(before.header)
        || resumed.parts.some(seq => seq < 3)) throw Error('resumed upload changed bytes/header or resent durably accepted prefix');
    await sender.evaluate(() => { window.__chunkAllowUpload = true; });
    await receiver.bringToFront();
    await receiver.waitForFunction(async id => (await window.__chunkInbox()).some(row => row.id === id && row.received === 0),
        { timeout: 90000, polling: 250 }, before.id).catch(error => { throw Error('native chunk durable-offer-admission: ' + error.message); });
    await sender.waitForFunction(async id => (await window.__chunkDrafts()).some(row => row.id === id && row.headerCommitted),
        { timeout: 30000, polling: 250 }, before.id);
    console.log(`  · native ${size}: offer/header saved with zero downloaded parts; restarting both endpoints`);
    await sourceGateway.restart(); await targetGateway.restart();
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver(); await receiver.bringToFront();
    const offerPrefix = await receiver.evaluate(async id => (await window.__chunkInbox()).find(row => row.id === id)?.received, before.id);
    if (offerPrefix !== 0) throw Error('offer restart lost its exact zero-part admission: ' + offerPrefix);
    await receiver.evaluate(() => { window.__chunkAllowFirstWindow = true; });
    await receiver.waitForFunction(async id => (await window.__chunkInbox()).some(row => row.id === id && row.received === 8),
        { timeout: 90000, polling: 250 }, before.id).catch(error => { throw Error('native chunk upload-to-receiver-prefix: ' + error.message); });
    await receiver.removeScriptToEvaluateOnNewDocument(receiveHook.identifier);
    receiveHook = await receiver.evaluateOnNewDocument(holdReceive, true);
    console.log(`  · native ${size}: eight receiver parts committed; restarting recipient`);
    await targetGateway.restart();
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    const savedPrefix = await receiver.evaluate(async id => (await window.__chunkInbox()).find(row => row.id === id)?.received, before.id);
    if (savedPrefix !== 8) throw Error('receiver reload lost its durable part prefix: ' + savedPrefix);
    const later = 'after-chunk-header-' + Date.now();
    await sender.bringToFront(); await sender.focus('#message-input'); await sender.type('#message-input', later); await sender.keyboard.press('Enter');
    await receiver.waitForFunction(text => document.getElementById('message-feed').textContent.includes(text), { timeout: 30000 }, later);
    await receiver.bringToFront(); await receiver.evaluate(() => { window.__chunkAllowDownload = true; });
    await receiver.waitForFunction(() => window.__chunkHistoryFailures > 0, { timeout: 90000 }).catch(error => { throw Error('native chunk complete-download-to-history-fault: ' + error.message); });
    console.log(`  · native ${size}: full ciphertext retained through failed history`);
    const pending = await receiver.evaluate(async ({ id, name }) => ({
        row: (await window.__chunkInbox()).filter(row => row.id === id).map(row => ({ received: row.received, total: row.manifest.chunk_count }))[0],
        history: (await (await import('./dmhistory.js')).dmHistorySearch(name)).length,
    }), { id: before.id, name });
    if (!pending.row || pending.row.received !== pending.row.total || pending.history !== 0) throw Error('failed history did not retain complete receive ciphertext: ' + JSON.stringify(pending));
    console.log(`  · native ${size}: restarting both endpoints before history can commit`);
    await sourceGateway.restart(); await targetGateway.restart();
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver(); await receiver.bringToFront();
    await receiver.waitForFunction(() => window.__chunkHistoryFailures > 0, { timeout: 60000 });
    const complete = await receiver.evaluate(async id => { const row = (await window.__chunkInbox()).find(row => row.id === id); return row && { received: row.received, total: row.manifest.chunk_count }; }, before.id);
    if (!complete || complete.received !== complete.total) throw Error('pre-history restart lost complete ciphertext');
    await receiver.evaluate(() => { window.__chunkAllowHistory = true; });
    await receiver.waitForFunction(() => window.__chunkLostCommits > 0, { timeout: 60000 });
    if (!await sender.evaluate(async id => (await window.__chunkDrafts()).some(row => row.id === id && !row.committed), before.id)) {
        throw Error('lost full receive acknowledgement retired the source');
    }
    console.log(`  · native ${size}: full commit lost; restarting both gateways and browsers`);
    await sourceGateway.restart(); await targetGateway.restart();
    await sender.removeScriptToEvaluateOnNewDocument(uploadHook.identifier);
    await receiver.removeScriptToEvaluateOnNewDocument(receiveHook.identifier);
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await sender.bringToFront();
    await sender.waitForFunction(async id => !(await window.__chunkDrafts()).some(row => row.id === id),
        { timeout: 90000, polling: 250 }, before.id).catch(error => { throw Error('native chunk final-source-retirement: ' + error.message); });
    for (const page of [sender, receiver]) {
        const result = await page.evaluate(async name => {
            const history = await import('./dmhistory.js'), rows = await history.dmHistorySearch(name);
            const record = rows[0] && await history.dmHistoryGet(rows[0].message_id);
            const bytes = Uint8Array.from(atob(record?.file?.data_b64 || ''), char => char.charCodeAt(0));
            return { count: rows.length, size: bytes.length,
                hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('') };
        }, name);
        if (result.count !== 1 || result.size !== size || result.hash !== expectedHash) throw Error('chunk durable history mismatch: ' + JSON.stringify(result));
    }
    await sender.removeScriptToEvaluateOnNewDocument(sourceReader.identifier);
    await receiver.removeScriptToEvaluateOnNewDocument(targetReader.identifier);
    console.log(`  ✓ native ${size}-byte file: source/gateway partial upload restart, receiver/gateway partial download restart, history failure, later text, lost full ACK and final full restart preserve exact ciphertext and one readable file per primary`);
}
