// Recipient choice is durable and distinct from a full-file delivery receipt.
export async function nativeDeclineJourney({ sender, receiver, sourceGateway, targetGateway, openSender, openReceiver, online }) {
    const name = 'native-declined-' + Date.now() + '.txt';
    const reader = () => {
        window.__declineScope = async () => {
            const did = localStorage.getItem('proxion_identity_did'), e2e = await import('./e2e.js');
            return JSON.stringify(['outgoing', did, did, e2e.e2eScopedKey('')]);
        };
        window.__declineDrafts = async () => (await import('./native-media-drafts.js')).createNativeMediaStore().list(await window.__declineScope());
        window.__declineInbox = async () => (await import('./native-media-inbox.js')).createNativeMediaInbox().list(await window.__declineScope());
    };
    const holdRecipient = () => {
        const send = WebSocket.prototype.send; let fetches = 0;
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* other frame */ }
            if (packet?.cmd === 'native_media_fetch' && ++fetches > 1) return;
            if (packet?.cmd === 'native_media_decline') { window.__lostDeclines = (window.__lostDeclines || 0) + 1; return; }
            return send.call(this, wire);
        };
    };
    const observeSource = () => {
        const send = WebSocket.prototype.send; window.__declinedSourceParts = 0;
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* other frame */ }
            if (packet?.cmd === 'native_media_part') window.__declinedSourceParts++;
            return send.call(this, wire);
        };
    };
    const sourceReader = await sender.evaluateOnNewDocument(reader), targetReader = await receiver.evaluateOnNewDocument(reader);
    const recipientHook = await receiver.evaluateOnNewDocument(holdRecipient);
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await sender.bringToFront();
    await sender.evaluate(name => {
        const data = new DataTransfer(); data.items.add(new File([new Uint8Array(2 * 1024 * 1024).fill(65)], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = data.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }, name);
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await sender.waitForFunction(async name => (await window.__declineDrafts()).some(row => row.metadata.name === name && row.stage === 'sealed'), { timeout: 30000 }, name);
    const before = await sender.evaluate(async name => {
        const row = (await window.__declineDrafts()).find(row => row.metadata.name === name);
        return { id: row.id, header: row.hdr,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].map(value => value.toString(16).padStart(2, '0')).join('') };
    }, name);
    await receiver.bringToFront();
    await receiver.waitForFunction(async id => (await window.__declineInbox()).some(row => row.id === id && row.received === 8), { timeout: 45000 }, before.id);
    await receiver.evaluate(() => document.getElementById('settings-btn').click());
    await receiver.evaluate(() => document.querySelector('[data-settings-target="settings-sec-pending"]').click());
    await receiver.waitForFunction(id => [...document.querySelectorAll('[data-outbox-action="decline"]')].some(button => button.dataset.outboxId.includes(id)), { timeout: 10000 }, before.id);
    await receiver.evaluate(id => [...document.querySelectorAll('[data-outbox-action="decline"]')].find(button => button.dataset.outboxId.includes(id)).focus(), before.id);
    await receiver.keyboard.press('Enter');
    await receiver.waitForFunction(() => window.__lostDeclines > 0, { timeout: 10000 });
    await receiver.waitForFunction(async id => !(await window.__declineInbox()).some(row => row.id === id), { timeout: 10000 }, before.id);
    const protectedReceipt = await receiver.evaluate(async id => {
        const receipts = (await import('./dm-receipts.js')).createDmReceiptStore(), scope = await window.__declineScope();
        const command = (await receipts.pendingCommits('native-decline:' + scope)).find(row => row.message_id === id);
        return { command, full: command && await receipts.has('native-media:' + scope, command.receive_id) };
    }, before.id);
    if (!protectedReceipt.command || protectedReceipt.full) throw Error('recipient decline lacked distinct protected evidence');
    const retained = await sender.evaluate(async id => { const row = (await window.__declineDrafts()).find(row => row.id === id); return row ? { committed: row.committed } : null; }, before.id);
    if (!retained || retained.committed) throw Error('lost decline acknowledgement fabricated full source delivery');
    await receiver.removeScriptToEvaluateOnNewDocument(recipientHook.identifier);
    const sourceObserver = await sender.evaluateOnNewDocument(observeSource);
    await sourceGateway.restart(); await targetGateway.restart();
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender(); await sender.bringToFront();
    await sender.waitForFunction(async id => (await window.__declineDrafts()).some(row => row.id === id && row.declined && row.headerCommitted && !row.committed),
        { timeout: 45000, polling: 250 }, before.id);
    await receiver.waitForFunction(async id => {
        const receipts = (await import('./dm-receipts.js')).createDmReceiptStore(), scope = await window.__declineScope();
        return !(await receipts.pendingCommits('native-decline:' + scope)).some(row => row.message_id === id);
    }, { timeout: 20000 }, before.id);
    const after = await sender.evaluate(async id => {
        const row = (await window.__declineDrafts()).find(row => row.id === id);
        return { header: row.hdr, parts: window.__declinedSourceParts,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].map(value => value.toString(16).padStart(2, '0')).join('') };
    }, before.id);
    if (after.parts || after.hash !== before.hash || JSON.stringify(after.header) !== JSON.stringify(before.header)) throw Error('declined source was retransmitted or replaced');
    for (const gateway of [sourceGateway, targetGateway]) {
        const row = gateway.nativeParts(before.id);
        if (row.status !== 'declined' || row.parts || !row.header_committed) throw Error('decline lost its exact terminal gateway evidence: ' + JSON.stringify(row));
    }
    const later = 'after-recipient-decline-' + Date.now();
    await sender.focus('#message-input'); await sender.type('#message-input', later); await sender.keyboard.press('Enter');
    await receiver.waitForFunction(text => document.getElementById('message-feed').textContent.includes(text), { timeout: 20000 }, later);
    for (const page of [sender, receiver]) {
        const count = await page.evaluate(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).length, name);
        if (count) throw Error('decline was recorded as a delivered history file');
    }
    await sender.removeScriptToEvaluateOnNewDocument(sourceObserver.identifier);
    await sender.removeScriptToEvaluateOnNewDocument(sourceReader.identifier);
    await receiver.removeScriptToEvaluateOnNewDocument(targetReader.identifier);
    console.log('  ✓ keyboard recipient decline survives lost acknowledgement and both gateway/browser restarts; ciphertext clears, no full history/delivery is claimed, original prepared source remains unchanged and later text decrypts');
}
