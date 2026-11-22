// Real primary browsers and gateways: interrupt source preparation, retain a
// receiver key through failed history, lose full commits, then restart both ends.
export async function nativeMediaJourney({ sender, receiver, sourceGateway, targetGateway, openSender, openReceiver, online }) {
    const name = 'native-durable-' + Date.now() + '.txt';
    const installReader = () => {
        window.__nativeDrafts = async () => {
            const did = localStorage.getItem('proxion_identity_did');
            const { e2eScopedKey } = await import('./e2e.js');
            return (await import('./native-media-drafts.js')).createNativeMediaStore().list(
                JSON.stringify(['outgoing', did, did, e2eScopedKey('')]));
        };
    };
    // evaluate serializes this closure-free function into each browser.
    const holdPrepared = name => {
        const put = IDBObjectStore.prototype.put;
        IDBObjectStore.prototype.put = function (row, ...args) {
            if (this.name === 'drafts' && row.metadata?.name === name && row.stage === 'sealed' && !window.__nativeAllowPrepared) {
                window.__nativePrepareFailures = (window.__nativePrepareFailures || 0) + 1;
                this.transaction.abort(); return;
            }
            return put.call(this, row, ...args);
        };
    };
    await sender.evaluate(installReader);
    const reader = await sender.evaluateOnNewDocument(installReader);
    await openSender(); await openReceiver();
    const holdReceiver = () => {
        const put = IDBObjectStore.prototype.put, send = WebSocket.prototype.send;
        window.__nativeRestoreHistory = () => { IDBObjectStore.prototype.put = put; };
        IDBObjectStore.prototype.put = function (...args) {
            if (this.name === 'messages') throw new DOMException('native receive history interruption', 'QuotaExceededError');
            return put.apply(this, args);
        };
        WebSocket.prototype.send = function (wire) {
            let command; try { command = JSON.parse(wire); } catch { /* other frame */ }
            if (command?.cmd?.startsWith('native_media_')) {
                window.__nativeCommands ||= {};
                window.__nativeCommands[command.cmd] = (window.__nativeCommands[command.cmd] || 0) + 1;
            }
            if (command?.cmd === 'native_media_commit') { window.__nativeLostCommits = (window.__nativeLostCommits || 0) + 1; return; }
            return send.call(this, wire);
        };
    };
    const loseCommits = await receiver.evaluateOnNewDocument(holdReceiver);
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await sender.evaluate(holdPrepared, name);
    const expectedHash = await sender.evaluate(async name => {
        const bytes = new Uint8Array(32 * 1024);
        for (let i = 0; i < bytes.length; i++) bytes[i] = 65 + i % 26;
        const transfer = new DataTransfer(); transfer.items.add(new File([bytes], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = transfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
    }, name);
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await sender.waitForFunction(() => window.__nativePrepareFailures > 0, { timeout: 20000 }).catch(error => { throw Error('prepare before reload: ' + error.message); });
    const before = await sender.evaluate(async name => {
        const row = (await window.__nativeDrafts()).find(row => row.metadata.name === name && row.stage === 'prepared');
        const e2e = await import('./e2e.js'), { dmEnvelopeFingerprint } = await import('./dm-receipts.js');
        const sendId = await dmEnvelopeFingerprint({ scope: row.scope, message_id: row.id, kind: 'native-inline-file' });
        return { id: row.id, ct: Array.from(row.prepared.ct), header: (await e2e.loadRatchetState(row.peer)).pendingSends[sendId] };
    }, name);
    if (!before.header) throw Error('native prepare crash missed the ratchet journal');
    const block = await sender.evaluateOnNewDocument(holdPrepared, name);
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await sender.waitForFunction(() => window.__nativePrepareFailures > 0, { timeout: 20000 }).catch(error => { console.error(sender._console.slice(-12).join('\n')); throw Error('prepare after reload: ' + error.message); });
    await sender.evaluate(() => document.getElementById('settings-btn').click());
    await sender.evaluate(() => document.querySelector('[data-settings-target="settings-sec-pending"]').click());
    const pendingId = 'native:' + before.id;
    await sender.waitForFunction(id => document.querySelector(`[data-outbox-id="${id}"]`), { timeout: 10000 }, pendingId);
    if (await sender.evaluate(id => !!document.querySelector(`[data-outbox-id="${id}"]`).closest('li').querySelector('[data-outbox-action="cancel"]'), pendingId)) {
        throw Error('spent native header offered unsafe cancellation');
    }
    await sender.evaluate(() => { window.__nativeAllowPrepared = true; });
    await sender.focus(`[data-outbox-id="${pendingId}"][data-outbox-action="retry"]`); await sender.keyboard.press('Enter');
    await sender.evaluate(() => document.getElementById('settings-cancel-btn').click());
    await sender.removeScriptToEvaluateOnNewDocument(block.identifier);
    await sender.waitForFunction(async id => (await window.__nativeDrafts()).some(row => row.id === id && row.headerCommitted),
        { timeout: 30000, polling: 250 }, before.id);
    const retained = await sender.evaluate(async id => {
        const row = (await window.__nativeDrafts()).find(row => row.id === id);
        return { ct: Array.from(row.prepared.ct), header: row.hdr, committed: row.committed };
    }, before.id);
    if (JSON.stringify(retained.ct) !== JSON.stringify(before.ct) || retained.header.content !== before.header.ciphertext
        || retained.header.nonce !== before.header.nonce || retained.header.msg_num !== before.header.msgNum || retained.committed) {
        throw Error('native retry changed ciphertext/header or falsely completed history');
    }
    const later = 'after-native-header-' + Date.now();
    await sender.focus('#message-input'); await sender.type('#message-input', later); await sender.keyboard.press('Enter');
    // The header hold is released while the file itself remains pending.
    await receiver.waitForFunction(text => document.getElementById('message-feed').textContent.includes(text), { timeout: 20000 }, later).catch(error => { console.error(receiver._console.slice(-12).join('\n')); throw Error('later text after header release: ' + error.message); });
    await receiver.bringToFront();
    await receiver.evaluate(() => window.__nativeRestoreHistory());
    await receiver.waitForFunction(() => window.__nativeLostCommits > 0, { timeout: 20000 }).catch(async error => {
        console.error('native recovery diagnostics', JSON.stringify(await receiver.evaluate(async name => ({
            historyEnabled: (await import('./dmhistory.js')).dmHistoryEnabled(),
            historyRows: (await (await import('./dmhistory.js')).dmHistorySearch(name)).length,
            commands: window.__nativeCommands,
            pending: (await (await import('./e2e.js')).pendingDmReceives({ transport: 'native-media' })).map(row => ({
                receiveId: row.receiveId, scope: row.data.scope, message: row.data.commit.message_id })),
        }), name)), JSON.stringify({ source: sourceGateway.nativeState(), target: targetGateway.nativeState() }));
        console.error(receiver._console.slice(-12).join('\n')); throw Error('full commit after restored history: ' + error.message);
    });
    await sourceGateway.restart(); await targetGateway.restart();
    await receiver.removeScriptToEvaluateOnNewDocument(loseCommits.identifier);
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await sender.waitForFunction(async id => !(await window.__nativeDrafts()).some(row => row.id === id),
        { timeout: 30000, polling: 250 }, before.id);
    for (const page of [sender, receiver]) {
        const result = await page.evaluate(async name => {
            const history = await import('./dmhistory.js'), rows = await history.dmHistorySearch(name);
            const record = rows[0] && await history.dmHistoryGet(rows[0].message_id);
            const bytes = Uint8Array.from(atob(record?.file?.data_b64 || ''), c => c.charCodeAt(0));
            return { count: rows.length, size: bytes.length,
                hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('') };
        }, name);
        if (result.count !== 1 || result.size !== 32 * 1024 || result.hash !== expectedHash) throw Error('native durable history mismatch: ' + JSON.stringify(result));
    }
    await sender.removeScriptToEvaluateOnNewDocument(reader.identifier);
    console.log('  ✓ native inline file: prepared-write crash, keyboard retry, failed receiver history, exact header release, lost commit and both gateway restarts preserve one exact file on each primary');
}
