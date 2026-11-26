// Actual upload/receive traffic; hooks only withhold parts or fail history writes.
export async function nativeFeedbackJourney({ sender, receiver, sourceGateway, openSender, openReceiver, online }) {
    const reader = () => {
        window.__feedbackScope = async () => {
            const did = localStorage.getItem('proxion_identity_did');
            return JSON.stringify(['outgoing', did, did, (await import('./e2e.js')).e2eScopedKey('')]);
        };
        window.__feedbackDrafts = async () => (await import('./native-media-drafts.js')).createNativeMediaStore().list(await window.__feedbackScope());
    };
    const hold = () => {
        const send = WebSocket.prototype.send;
        window.__feedbackHeld = null;
        WebSocket.prototype.send = function (wire) {
            let packet; try { packet = JSON.parse(wire); } catch { /* other frame */ }
            if (packet?.cmd === 'native_media_part' && !window.__feedbackAllow) {
                window.__feedbackHeld = { socket: this, wire, packet }; return;
            }
            return send.call(this, wire);
        };
        window.__feedbackRelease = () => {
            const held = window.__feedbackHeld; window.__feedbackHeld = null;
            if (held) send.call(held.socket, held.wire);
        };
    };
    const failHistory = () => {
        const put = IDBObjectStore.prototype.put;
        IDBObjectStore.prototype.put = function (...args) {
            if (this.name === 'native_files' && !window.__feedbackHistory) {
                window.__feedbackHistoryFailed = true; this.transaction.abort(); return;
            }
            return put.apply(this, args);
        };
    };
    const hooks = [await sender.evaluateOnNewDocument(reader), await sender.evaluateOnNewDocument(hold)];
    const targetHook = await receiver.evaluateOnNewDocument(failHistory);
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    await sender.bringToFront();
    const name = 'native-live-feedback-' + Date.now() + '.txt';
    await sender.evaluate(name => {
        const data = new DataTransfer(); data.items.add(new File([new Uint8Array(600 * 1024).fill(71)], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = data.files; input.dispatchEvent(new Event('change', { bubbles: true }));
    }, name);
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await sender.waitForFunction(() => !!window.__feedbackHeld, { timeout: 30000 });
    console.log('  · feedback upload held; opening live review');
    const before = await sender.evaluate(async name => {
        const row = (await window.__feedbackDrafts()).find(row => row.metadata.name === name);
        return { id: row.id, header: row.hdr, bytes: row.prepared.ct.length,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].join(',') };
    }, name);
    const showPending = async page => {
        await page.click('#settings-btn');
        await page.click('[data-settings-target="settings-sec-pending"]');
    };
    const retry = `[data-outbox-id="native:${before.id}"][data-outbox-action="retry"]`;
    const row = `[data-outbox-row="native:${before.id}"]`;
    await showPending(sender); await sender.waitForSelector(retry, { visible: true }); await sender.focus(retry);
    await sender.evaluate(selector => { window.__feedbackButton = document.querySelector(selector); }, retry);
    for (let seq = 0; seq < 3; seq++) {
        await sender.waitForFunction(seq => window.__feedbackHeld?.packet.seq === seq, { timeout: 15000 }, seq);
        await sender.evaluate(() => window.__feedbackRelease());
        await sender.waitForFunction(({ row, seq }) => Number(document.querySelector(row + ' progress')?.value) >= seq + 1,
            { timeout: 10000 }, { row, seq });
        if (!await sender.evaluate(() => document.activeElement === window.__feedbackButton && window.__feedbackButton.isConnected)) throw Error('progress rebuilt or defocused Retry');
    }
    // Fault injection at the authenticated socket adapter: a foreign transfer
    // cannot change this row; an exact outstanding-part rate refusal can.
    await sender.evaluate(id => {
        const held = window.__feedbackHeld;
        held.socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'error', code: 'E_RATE', cmd: 'native_media_part',
            message: 'rate_limited', message_id: id, seq: held.packet.seq }) }));
    }, before.id);
    await sender.waitForFunction(row => document.querySelector(row)?.textContent.includes('gateway is busy'), { timeout: 10000 }, row);
    if (!await sender.evaluate(() => document.activeElement === window.__feedbackButton)) throw Error('refusal stole keyboard focus');
    console.log('  · feedback progress and rate status retained keyboard focus; restarting source');
    await sourceGateway.pause();
    await sender.waitForFunction(row => document.querySelector(row)?.textContent.includes('Reconnect a supported'), { timeout: 15000 }, row);
    await sourceGateway.resume(); await online(sender);
    await sender.waitForSelector(retry + ':not([disabled]):not([aria-disabled="true"])', { timeout: 30000 }).catch(async error => {
        console.error('feedback reconnect diagnostic', JSON.stringify(await sender.evaluate(() => ({ rows: document.getElementById('dm-outbox-rows')?.innerHTML,
            settings: document.getElementById('settings-modal')?.style.display, status: document.getElementById('dm-outbox-status')?.textContent,
            focus: document.activeElement?.id, button: window.__feedbackButton?.isConnected }))));
        console.error((sender._console || []).slice(-12).join('\n')); throw error;
    });
    if (!await sender.evaluate(() => window.__feedbackButton.isConnected && document.activeElement === window.__feedbackButton)) { console.error('feedback focus', JSON.stringify(await sender.evaluate(() => ({ connected: window.__feedbackButton.isConnected, active: document.activeElement?.outerHTML?.slice(0, 400) })))); throw Error('reconnect replaced focused Retry'); }
    const resumed = await sender.evaluate(async id => {
        const row = (await window.__feedbackDrafts()).find(row => row.id === id);
        return { header: row.hdr, bytes: row.prepared.ct.length,
            hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct))].join(',') };
    }, before.id);
    if (JSON.stringify(resumed) !== JSON.stringify({ header: before.header, bytes: before.bytes, hash: before.hash })) throw Error('feedback retry changed prepared source/header');
    await sender.evaluate(() => { window.__feedbackAllow = true; window.__feedbackRelease(); });
    await sender.keyboard.press('Enter');
    await receiver.bringToFront(); await receiver.waitForFunction(() => window.__feedbackHistoryFailed, { timeout: 45000 });
    await showPending(receiver);
    await receiver.waitForFunction(() => document.querySelector('#dm-outbox-rows')?.textContent.includes('Restore storage access'), { timeout: 15000 });
    const history = '#dm-outbox-rows [data-outbox-action="history"]'; await receiver.waitForSelector(history, { visible: true }); await receiver.focus(history); await receiver.keyboard.press('Enter');
    await receiver.waitForFunction(() => document.activeElement?.id === 'settings-dm-history-toggle');
    await receiver.evaluate(() => { window.__feedbackHistory = true; });
    await receiver.click('[data-settings-target="settings-sec-pending"]');
    const incomingRetry = `[data-outbox-action="retry"][data-outbox-id*="${before.id}"]`;
    await receiver.waitForSelector(incomingRetry, { visible: true }); await receiver.focus(incomingRetry); await receiver.keyboard.press('Enter');
    await receiver.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).some(row => row.file), { timeout: 45000 }, name);
    await sender.waitForFunction(async id => !(await window.__feedbackDrafts()).some(row => row.id === id), { timeout: 45000 }, before.id);
    await receiver.click('#settings-cancel-btn'); await sender.bringToFront(); await sender.click('#settings-cancel-btn');
    for (const hook of hooks) await sender.removeScriptToEvaluateOnNewDocument(hook.identifier);
    await receiver.removeScriptToEvaluateOnNewDocument(targetHook.identifier);
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await receiver.reload({ waitUntil: 'load' }); await online(receiver); await openReceiver();
    console.log('  ✓ live Pending sends progress/refusal/reconnect preserve focused Retry and exact source; history failure retains receive state and links to preferences before successful retry');
}
