// An external owner client deletes a consumed drop while this device cannot
// acknowledge it. Reload must reconcile the durable receipt with HEAD only.
export async function dmReceiptReconciliationJourney({ sender, receiver, send, openReceiver, externalFetch, label }) {
    await openReceiver();
    const desktop = await receiver.evaluate(() => !!window.proxionCrossMode?.isReady());
    const blocked = new Set(); let intercept;
    if (desktop) {
        await receiver.evaluate(() => {
            window.__receiptBlockedUrls = [];
            window.proxionCrossMode.bridge.io.podDeleteDmDrop = async url => { window.__receiptBlockedUrls.push(url); return false; };
        });
    } else {
        await receiver.setRequestInterception(true);
        intercept = request => {
            if (request.method() === 'DELETE' && request.url().includes('/proxion/dm-inbox/')) {
                blocked.add(request.url()); request.respond({ status: 503, body: 'external-removal probe' }).catch(() => {});
            } else request.continue().catch(() => {});
        };
        receiver.on('request', intercept);
    }
    const text = label + '-external-removal';
    const sent = await send(sender, text); if (sent !== true) throw Error('receipt probe delivery failed');
    const drain = () => receiver.evaluate(() => (window.proxionWebDm || window.proxionCrossMode).drainOnce());
    for (let index = 0; index < 20; index++) {
        await drain();
        if (await receiver.evaluate(text => document.getElementById('message-feed').textContent.includes(text), text)) break;
        await new Promise(resolve => setTimeout(resolve, 300));
    }
    if (desktop) for (const url of await receiver.evaluate(() => window.__receiptBlockedUrls)) blocked.add(url);
    if (!blocked.size) throw Error('receipt probe did not retain a drop');
    const readEvidence = async urls => receiver.evaluate(async urls => {
        const db = await new Promise((resolve, reject) => {
            const request = indexedDB.open('proxion-dm-receipts-v1'); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
        });
        try {
            const rows = await new Promise((resolve, reject) => {
                const request = db.transaction('receipts').objectStore('receipts').getAll(); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
            });
            return rows.filter(row => urls.includes(row.url)).map(({ key, acknowledged }) => ({ key, acknowledged }));
        } finally { db.close(); }
    }, urls);
    const before = await readEvidence([...blocked]);
    if (!before.length || before.some(row => row.acknowledged)) throw Error('pending receipt evidence missing');
    for (const url of blocked) {
        const response = await externalFetch(url, { method: 'DELETE' });
        if (!response.ok) throw Error('external drop deletion failed: ' + response.status);
    }
    if (intercept) { await receiver.setRequestInterception(false); receiver.off('request', intercept); }
    const metadata = [];
    receiver.on('request', request => { if (blocked.has(request.url())) metadata.push(request.method()); });
    if (desktop) {
        const cdp = await receiver.createCDPSession(); await cdp.send('Network.enable');
        cdp.on('Network.webSocketFrameSent', event => {
            try { const message = JSON.parse(event.response.payloadData); if (message.cmd === 'pod_fetch' && blocked.has(message.url)) metadata.push(message.method); } catch { /* other frame */ }
        });
    }
    await receiver.reload({ waitUntil: 'networkidle2' });
    await receiver.waitForFunction(() => !!window.proxionWebDm || window.proxionCrossMode?.isReady(), { timeout: 40000 });
    await openReceiver(); await drain();
    const after = await readEvidence([...blocked]);
    if (after.length !== before.length || after.some(row => !row.acknowledged)) throw Error('missing drop did not release pending receipt');
    if (!metadata.includes('HEAD') || metadata.some(method => !['HEAD', 'OPTIONS'].includes(method))) throw Error('reconciliation did more than inspect metadata: ' + metadata.join(','));
    await receiver.waitForFunction(text => document.getElementById('message-feed').textContent.includes(text), { timeout: 20000 }, text);
    if (await send(sender, text + '-next') !== true) throw Error('new DM failed after reconciliation');
    for (let attempt = 0; attempt < 20; attempt++) {
        await drain();
        if (await receiver.evaluate(text => document.getElementById('message-feed').textContent.includes(text), text + '-next')) {
            console.log(`  ✓ ${label}: external deletion/reload releases pending receipt using HEAD, preserves history, and receives the next DM`);
            return;
        }
        await new Promise(resolve => setTimeout(resolve, 300));
    }
    throw Error('new DM did not arrive after reconciliation');
}


// Interrupt history persistence after the ratchet advances, then reload. The
// encrypted recovery entry must bridge the gap before the inbox is acknowledged.
export async function dmReceiveRecoveryJourney({ sender, receiver, send, openReceiver, label }) {
    await openReceiver();
    const text = label + '-history-interruption';
    await receiver.evaluate(() => {
        const put = IDBObjectStore.prototype.put;
        window.__historyWriteFailures = 0;
        IDBObjectStore.prototype.put = function (...args) {
            if (this.name === 'messages') {
                window.__historyWriteFailures++;
                throw new DOMException('injected history storage failure', 'QuotaExceededError');
            }
            return put.apply(this, args);
        };
    });
    if (await send(sender, text) !== true) throw Error('recovery probe was not sent');
    const drain = () => receiver.evaluate(() => (window.proxionWebDm || window.proxionCrossMode).drainOnce());
    await receiver.waitForFunction(text => document.getElementById('message-feed').textContent.includes(text), { timeout: 25000 }, text);
    await drain();
    const before = await receiver.evaluate(async text => {
        const cross = window.proxionCrossMode;
        const drops = cross?.isReady() ? await cross.bridge.io.podReadDmDrops()
            : await (await import('./pod.js')).podReadDmDrops();
        const { loadRatchetState } = await import('./e2e.js');
        const pending = [];
        for (const drop of drops) {
            const env = drop.envelope;
            const peer = env.kind === 'fanout' ? env.from_webid + '#' + (env.payload?.from_device_id || env.from_webid) : env.from_webid;
            const state = await loadRatchetState(peer);
            if (Object.values(state?.pendingReceives || {}).includes(text)) pending.push(drop.url);
        }
        return { failures: window.__historyWriteFailures, pending,
            copies: document.getElementById('message-feed').textContent.split(text).length - 1 };
    }, text);
    if (!before.failures || before.pending.length !== 1 || before.copies !== 1) {
        throw Error('interrupted receive did not retain one recovery copy: ' + JSON.stringify(before));
    }
    await receiver.reload({ waitUntil: 'networkidle2' });
    await receiver.waitForFunction(() => !!window.proxionWebDm || window.proxionCrossMode?.isReady(), { timeout: 40000 });
    await openReceiver(); await drain();
    await receiver.waitForFunction(text => document.getElementById('message-feed').textContent.includes(text), { timeout: 25000 }, text);
    const after = await receiver.evaluate(async ({ text, pending }) => {
        const cross = window.proxionCrossMode;
        const drops = cross?.isReady() ? await cross.bridge.io.podReadDmDrops()
            : await (await import('./pod.js')).podReadDmDrops();
        const { dmHistorySearch } = await import('./dmhistory.js');
        const rows = await dmHistorySearch(text);
        return { retained: drops.some(drop => pending.includes(drop.url)),
            saved: rows.filter(row => row.content === text).length,
            copies: document.getElementById('message-feed').textContent.split(text).length - 1 };
    }, { text, pending: before.pending });
    if (after.retained || after.saved !== 1 || after.copies !== 1) throw Error('receive did not recover cleanly: ' + JSON.stringify(after));
    console.log(`  ✓ ${label}: interrupted history recovers after reload, keeps one message, and acknowledges the drop`);
}
