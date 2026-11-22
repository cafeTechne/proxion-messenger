// An inline-only client can have accepted ciphertext that exceeds the signed
// HTTP envelope cap. Upgrading transport must preserve that original receipt.
export async function nativeInlineAdaptationJourney({ sender, receiver, sourceGateway, targetGateway, openSender, openReceiver, online }) {
    const name = 'native-old-inline-' + Date.now() + '.txt', size = 400 * 1024;
    const reader = () => {
        window.__inlineDrafts = async () => {
            const did = localStorage.getItem('proxion_identity_did'), e2e = await import('./e2e.js');
            return (await import('./native-media-drafts.js')).createNativeMediaStore().list(
                JSON.stringify(['outgoing', did, did, e2e.e2eScopedKey('')]));
        };
    };
    const inlineOnly = () => {
        const send = WebSocket.prototype.send;
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* other frame */ }
            if (packet?.cmd === 'register') { delete packet.native_chunks_v; wire = JSON.stringify(packet); }
            return send.call(this, wire);
        };
    };
    const sourceReader = await sender.evaluateOnNewDocument(reader), oldClient = await sender.evaluateOnNewDocument(inlineOnly);
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender(); await openReceiver();
    await sender.bringToFront();
    const expectedHash = await sender.evaluate(async ({ name, size }) => {
        const bytes = new Uint8Array(size);
        for (let i = 0; i < bytes.length; i++) bytes[i] = 65 + i % 26;
        const data = new DataTransfer(); data.items.add(new File([bytes], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = data.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
    }, { name, size });
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await sender.waitForFunction(async name => (await window.__inlineDrafts()).some(row => row.metadata.name === name && row.stage === 'sealed'),
        { timeout: 30000 }, name);
    const before = await sender.evaluate(async name => {
        const row = (await window.__inlineDrafts()).find(row => row.metadata.name === name);
        return { id: row.id, header: row.hdr,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].map(value => value.toString(16).padStart(2, '0')).join('') };
    }, name);
    let original;
    for (let attempt = 0; attempt < 40; attempt++) {
        original = sourceGateway.nativeParts(before.id);
        if (original.transport === 'inline') break;
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (original.transport !== 'inline' || original.status !== 'pending' || original.header_committed
        || targetGateway.nativeParts(before.id).transport) throw Error('did not preserve oversized accepted inline custody before upgrade');
    // Upgrade only the connection's negotiated transport, keeping the same
    // durable source, file key, header, gateway record and message identity.
    await sender.removeScriptToEvaluateOnNewDocument(oldClient.identifier);
    await sourceGateway.restart();
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await receiver.bringToFront();
    await receiver.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).length === 1,
        { timeout: 45000, polling: 250 }, name);
    await sender.bringToFront();
    await sender.waitForFunction(async id => !(await window.__inlineDrafts()).some(row => row.id === id),
        { timeout: 45000, polling: 250 }, before.id);
    const adapted = sourceGateway.nativeParts(before.id), received = targetGateway.nativeParts(before.id);
    if (adapted.transport !== 'chunks' || adapted.status !== 'committed' || received.transport !== 'chunks'
        || original.queue_id !== adapted.queue_id || original.envelope_digest !== adapted.envelope_digest
        || original.header_digest !== adapted.header_digest) throw Error('inline upgrade replaced accepted receipt/header identity');
    if (received.cipher_digest !== before.hash) throw Error('adapted ciphertext digest changed');
    for (const page of [sender, receiver]) {
        await page.reload({ waitUntil: 'load' }); await online(page);
        const result = await page.evaluate(async name => {
            const history = await import('./dmhistory.js'), rows = await history.dmHistorySearch(name);
            const record = rows[0] && await history.dmHistoryGet(rows[0].message_id);
            const bytes = Uint8Array.from(atob(record?.file?.data_b64 || ''), char => char.charCodeAt(0));
            return { count: rows.length, size: bytes.length,
                hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('') };
        }, name);
        if (result.count !== 1 || result.size !== size || result.hash !== expectedHash) throw Error('adapted inline history mismatch: ' + JSON.stringify(result));
    }
    await openSender(); await openReceiver();
    await sender.removeScriptToEvaluateOnNewDocument(sourceReader.identifier);
    console.log('  ✓ accepted 400 KiB inline file upgrades after gateway/browser restart with original queue/envelope/header identity, identical ciphertext and one readable history file per primary');
}
