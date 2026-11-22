export async function nativeGifJourney({ sender, receiver, openSender, openReceiver, online }) {
    const name = 'native-tray-' + Date.now() + '.gif';
    const bytes = 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
    await openSender(); await openReceiver(); await sender.bringToFront();
    const saved = await sender.evaluate(async ({ name, bytes }) => (await import('./gifs.js')).saveFavorite({ filename: name, mime: 'image/gif', data_b64: bytes }), { name, bytes });
    if (saved !== 'saved') throw Error('GIF fixture did not enter the real account-scoped tray');
    if (!await sender.$eval('#gif-tray-btn', button => !!button.getClientRects().length)) {
        await sender.click('label[for="compose-more-toggle"]');
    }
    await sender.click('#gif-tray-btn'); await sender.waitForSelector('#gif-tray .gif-send', { visible: true });
    await sender.focus('#gif-tray .gif-send'); await sender.keyboard.press('Enter');
    await receiver.bringToFront();
    await receiver.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).length === 1, { timeout: 30000 }, name);
    await sender.bringToFront();
    await sender.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).length === 1, { timeout: 30000 }, name);
    for (const page of [sender, receiver]) {
        await page.reload({ waitUntil: 'load' }); await online(page);
        const file = await page.evaluate(async name => {
            const history = await import('./dmhistory.js'), rows = await history.dmHistorySearch(name);
            return { count: rows.length, file: rows[0] && (await history.dmHistoryGet(rows[0].message_id))?.file };
        }, name);
        if (file.count !== 1 || file.file?.mime_type !== 'image/gif' || file.file?.data_b64 !== bytes) throw Error('GIF tray reload lost exact native file: ' + JSON.stringify(file));
    }
    await openSender(); await openReceiver();
    console.log('  ✓ real keyboard GIF-tray send uses sealed native attachments and restores one exact GIF per primary after reload');
}

// Run last: blocking a spent, unconsumed header intentionally retains that
// original prepared source rather than resetting keys to make later sends work.
export async function nativeBlockJourney({ sender, receiver, sourceGateway, targetGateway, openSender, online }) {
    const name = 'native-policy-block-' + Date.now() + '.txt';
    const inspect = () => {
        window.__policyDrafts = async () => {
            const did = localStorage.getItem('proxion_identity_did'), e2e = await import('./e2e.js');
            return (await import('./native-media-drafts.js')).createNativeMediaStore().list(JSON.stringify(['outgoing', did, did, e2e.e2eScopedKey('')]));
        };
    };
    const stopUpload = () => {
        const send = WebSocket.prototype.send; window.__policyParts = [];
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* other frame */ }
            if (packet?.cmd === 'native_media_part') { window.__policyParts.push(packet.seq); if (packet.seq >= 3) return; }
            return send.call(this, wire);
        };
    };
    const reader = await sender.evaluateOnNewDocument(inspect), hook = await sender.evaluateOnNewDocument(stopUpload);
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender(); await sender.bringToFront();
    await sender.evaluate(name => {
        const data = new DataTransfer(); data.items.add(new File([new Uint8Array(2 * 1024 * 1024).fill(65)], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = data.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }, name);
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await sender.waitForFunction(() => window.__policyParts.includes(3), { timeout: 30000 });
    const before = await sender.evaluate(async name => {
        const row = (await window.__policyDrafts()).find(row => row.metadata.name === name);
        return { id: row.id, header: row.hdr,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].map(value => value.toString(16).padStart(2, '0')).join('') };
    }, name);
    if (sourceGateway.nativeParts(before.id).parts !== 3) throw Error('block test missed durable partial upload');
    await sender.click('#dm-list li.dm-item', { button: 'right' });
    await sender.waitForSelector('#sidebar-ctx-menu', { visible: true }); await sender.click('#sctx-block');
    await sender.waitForFunction(async id => (await window.__policyDrafts()).some(row => row.id === id && row.blocked && !row.committed), { timeout: 20000 }, before.id);
    let row = sourceGateway.nativeParts(before.id);
    if (row.status !== 'blocked' || row.parts || row.header_committed || targetGateway.nativeParts(before.id).transport) throw Error('block did not remove exact partial gateway custody without false progress');
    await sourceGateway.restart(); await targetGateway.restart();
    await sender.removeScriptToEvaluateOnNewDocument(hook.identifier);
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    const retained = await sender.evaluate(async id => {
        const row = (await window.__policyDrafts()).find(row => row.id === id);
        return row && { blocked: row.blocked, committed: !!row.committed, header: row.hdr,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].map(value => value.toString(16).padStart(2, '0')).join('') };
    }, before.id);
    row = sourceGateway.nativeParts(before.id);
    if (!retained?.blocked || retained.committed || retained.hash !== before.hash || JSON.stringify(retained.header) !== JSON.stringify(before.header)
        || row.status !== 'blocked' || row.parts || targetGateway.nativeParts(before.id).transport) throw Error('block/restart resurrected or replaced prepared custody');
    await sender.evaluate(() => document.getElementById('settings-btn').click());
    await sender.evaluate(() => document.querySelector('[data-settings-target="settings-sec-pending"]').click());
    await sender.waitForFunction(id => [...document.querySelectorAll('[data-outbox-id]')].some(button => button.dataset.outboxId === 'native:' + id), { timeout: 10000 }, before.id);
    const actions = await sender.evaluate(id => [...document.querySelectorAll('[data-outbox-id]')].filter(button => button.dataset.outboxId === 'native:' + id)
        .map(button => ({ action: button.dataset.outboxAction, disabled: button.disabled })), before.id);
    if (actions.some(button => button.action === 'cancel' || button.action === 'retry' && !button.disabled)) throw Error('blocked prepared source offered unsafe cancellation or upload retry');
    for (const page of [sender, receiver]) {
        if (await page.evaluate(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).length, name)) throw Error('blocked upload fabricated a delivered history file');
    }
    await sender.removeScriptToEvaluateOnNewDocument(reader.identifier);
    console.log('  ✓ real sidebar block purges partial native ciphertext, survives both gateway/browser restart, keeps the exact spent source and offers neither false delivery nor unsafe cancellation');
}

export async function auditNativeVoiceHistory(pages) {
    const recorded = [];
    for (const page of pages) {
        if (!/^http:\/\/127\.0\.0\.1:/.test(page.url())) continue;
        const voice = await page.evaluate(async () => {
            const history = await import('./dmhistory.js'), rows = await history.dmHistorySearch('voice.webm');
            if (!rows.length) return null;
            const file = (await history.dmHistoryGet(rows[0].message_id))?.file;
            const bytes = Uint8Array.from(atob(file?.data_b64 || ''), char => char.charCodeAt(0));
            return { count: rows.length, size: bytes.length, mime: file?.mime_type,
                hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('') };
        });
        if (voice) recorded.push(voice);
    }
    if (recorded.length !== 2 || recorded.some(voice => voice.count !== 1 || voice.size < 1 || voice.mime !== 'audio/webm')
        || recorded[0].size !== recorded[1].size || recorded[0].hash !== recorded[1].hash) throw Error('native microphone recording history is missing, duplicated or changed: ' + JSON.stringify(recorded));
    console.log('  ✓ microphone recording restores one identical non-empty WebM history file on both primaries after repeated reloads');
}
