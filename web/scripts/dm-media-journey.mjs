// Real chat controls, durable drafts, encrypted delivery, verified download,
// reload and actual MediaRecorder audio. Shared by browser and cross-mode DMs.
export async function dmMediaJourney({ sender, receiver, openSender, openReceiver, label }) {
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
    const ready = async page => {
        await page.waitForFunction(() => !!window.proxionWebDm || window.proxionCrossMode?.isReady(), { timeout: 40000 });
    };
    const drain = page => page.evaluate(() => (window.proxionWebDm || window.proxionCrossMode)?.drainOnce());
    const until = async (page, check, arg) => {
        for (let attempt = 0; attempt < 25; attempt++) {
            await drain(page);
            if (await page.evaluate(check, arg)) return;
            await pause(500);
        }
        throw Error('DM media condition timed out: ' + label);
    };
    await openSender(); await openReceiver();
    const name = label + '.txt', text = 'private DM bytes '.repeat(3000);
    const desktop = await sender.evaluate(() => !!window.proxionCrossMode?.isReady());
    let failed = false, deliveryFailed = false, intercept;
    if (desktop) {
        await sender.evaluate(() => {
            const bridge = window.proxionCrossMode.bridge, original = bridge.fetch;
            let failed = false;
            bridge.fetch = (url, options) => {
                if (!failed && options?.method === 'PUT' && url.includes('/proxion/dm-assets/asset-')) {
                    failed = true; return Promise.reject(Error('simulated upload outage'));
                }
                return original(url, options);
            };
        });
    } else {
        await sender.setRequestInterception(true);
        intercept = request => {
            if (!failed && request.method() === 'PUT' && request.url().includes('/proxion/dm-assets/asset-')) {
                failed = true; request.abort('failed').catch(() => {});
            } else if (!deliveryFailed && request.method() === 'POST' && request.url().includes('/proxion/dm-inbox/')) {
                deliveryFailed = true;
                sender.evaluate(() => { window.__dmMediaDeliveryFailed = true; }).then(() => request.abort('failed')).catch(() => {});
            } else request.continue().catch(() => {});
        };
        sender.on('request', intercept);
    }
    await sender.evaluate(({ name, text }) => {
        const transfer = new DataTransfer(); transfer.items.add(new File([text], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = transfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }, { name, text });
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await sender.waitForSelector('[data-dm-media-retry]:not([disabled])', { timeout: 30000 });
    if (!await sender.$('[data-dm-media-cleanup]')) throw Error('Never-published upload did not offer cleanup');
    const id = await sender.$eval('[data-dm-media-draft]', element => element.dataset.dmMediaDraft.split('\n').at(-1));
    await sender.reload({ waitUntil: 'networkidle2' }); await ready(sender); await openSender();
    await sender.waitForSelector('[data-dm-media-retry]:not([disabled])', { timeout: 20000 });
    if (desktop) {
        await sender.evaluate(() => {
            const io = window.proxionCrossMode.bridge.io, original = io.podDropDm;
            let failed = false;
            io.podDropDm = (...args) => {
                if (!failed) {
                    failed = true; window.__dmMediaDeliveryFailed = true;
                    return Promise.reject(Error('simulated delivery outage'));
                }
                return original(...args);
            };
        });
    }
    await sender.click('[data-dm-media-retry]');
    await sender.waitForFunction(() => window.__dmMediaDeliveryFailed === true, { timeout: 30000 });
    await sender.waitForSelector('[data-dm-media-retry]:not([disabled])', { timeout: 20000 });
    if (await sender.$('[data-dm-media-cleanup]')) throw Error('Possibly delivered upload offered unsafe cleanup');
    await sender.reload({ waitUntil: 'networkidle2' }); await ready(sender); await openSender();
    await sender.waitForSelector('[data-dm-media-retry]:not([disabled])', { timeout: 20000 });
    await sender.click('[data-dm-media-retry]');
    await sender.waitForFunction(() => document.getElementById('dm-media-drafts').hidden, { timeout: 30000 });
    if (intercept) { await sender.setRequestInterception(false); sender.off('request', intercept); }
    await until(receiver, id => !!document.getElementById('msg-' + id)?.querySelector('[data-dm-asset]:not([disabled])'), id);
    await receiver.bringToFront();
    await receiver.$$eval('.toast__close', buttons => buttons.forEach(button => button.click()));
    await receiver.evaluate(() => {
        window.__dmReadTrace = [];
        document.addEventListener('click', event => {
            window.__dmReadClick = { tag: event.target.tagName, id: event.target.id, className: event.target.className,
                asset: event.target.closest('[data-dm-asset]')?.dataset.dmAsset };
        }, { capture: true, once: true });
        if (!window.proxionCrossMode?.isReady()) return;
        const bridge = window.proxionCrossMode.bridge, original = bridge.fetch;
        bridge.fetch = async (url, options) => {
            const response = await original(url, options);
            if (url.includes('/proxion/dm-assets/')) window.__dmReadTrace.push({ method: options?.method || 'GET', status: response.status, error: response.error });
            return response;
        };
    });
    await receiver.click(`#msg-${id} [data-dm-asset]`);
    const checkBytes = ({ name, text }) => document.querySelector(`a[download="${name}"]`)?.href === 'data:application/octet-stream;base64,' + btoa(text);
    try { await receiver.waitForFunction(checkBytes, { timeout: 20000 }, { name, text }); }
    catch (error) {
        console.error('DM download diagnostics:', await receiver.evaluate(({ id, name }) => ({
            card: document.getElementById('msg-' + id)?.textContent,
            disabled: document.querySelector(`#msg-${id} [data-dm-asset]`)?.disabled,
            bytes: document.querySelector(`a[download="${name}"]`)?.href.length,
            toasts: [...document.querySelectorAll('.toast__msg')].map(element => element.textContent),
            ready: window.proxionCrossMode?.isReady(), trace: window.__dmReadTrace, click: window.__dmReadClick,
        }), { id, name }));
        throw error;
    }
    await receiver.reload({ waitUntil: 'networkidle2' }); await ready(receiver); await openReceiver();
    await until(receiver, id => !!document.getElementById('msg-' + id), id);
    const load = await receiver.$(`#msg-${id} [data-dm-asset]:not([disabled])`);
    if (load) await load.click();
    await receiver.waitForFunction(checkBytes, { timeout: 20000 }, { name, text });
    console.log(`  ✓ ${label}: upload and delivery failures retain the draft across reload; retry delivers one stable message; exact file bytes restore`);

    await openSender();
    await sender.click('#voice-record-btn');
    await sender.waitForFunction(() => document.getElementById('voice-recording-bar').classList.contains('active'));
    await pause(1000); await sender.click('#voice-record-btn');
    await until(receiver, () => [...document.querySelectorAll('[data-dm-asset]:not([disabled])')].some(button => button.parentElement.textContent.includes('voice.webm')));
    await receiver.evaluate(() => [...document.querySelectorAll('[data-dm-asset]:not([disabled])')].find(button => button.parentElement.textContent.includes('voice.webm')).click());
    await receiver.waitForFunction(() => [...document.querySelectorAll('.audio-message audio')].some(audio => audio.readyState >= 1), { timeout: 20000 });
    await receiver.evaluate(async () => { const audio = [...document.querySelectorAll('.audio-message audio')].at(-1); await audio.play(); audio.pause(); });
    await receiver.reload({ waitUntil: 'networkidle2' }); await ready(receiver); await openReceiver();
    await until(receiver, () => !!document.querySelector('.audio-message audio'));
    await receiver.waitForFunction(() => document.querySelector('.audio-message audio').readyState >= 1, { timeout: 20000 });
    console.log(`  ✓ ${label}: real recorded voice decrypts, plays and survives reload`);

    await sender.bringToFront(); await openSender();
    let failUpload = false, failDelete = false, cleanupInterceptor;
    if (desktop) {
        await sender.evaluate(() => {
            const bridge = window.proxionCrossMode.bridge, original = bridge.fetch;
            let upload = false, deletion = false;
            bridge.fetch = (url, options) => {
                if (!upload && options?.method === 'PUT' && url.endsWith('-3.json')) { upload = true; return Promise.reject(Error('partial upload failure')); }
                if (!deletion && options?.method === 'DELETE' && url.endsWith('-1.json')) { deletion = true; return Promise.reject(Error('cleanup failure')); }
                return original(url, options);
            };
        });
    } else {
        await sender.setRequestInterception(true);
        cleanupInterceptor = request => {
            if (!failUpload && request.method() === 'PUT' && request.url().endsWith('-3.json')) {
                failUpload = true; request.abort('failed').catch(() => {});
            } else if (!failDelete && request.method() === 'DELETE' && request.url().endsWith('-1.json')) {
                failDelete = true; request.abort('failed').catch(() => {});
            } else request.continue().catch(() => {});
        };
        sender.on('request', cleanupInterceptor);
    }
    await sender.evaluate(label => {
        const transfer = new DataTransfer(); transfer.items.add(new File([new Uint8Array(110000)], label + '-unused.txt', { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
    }, label);
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await sender.waitForSelector('[data-dm-media-cleanup]:not([disabled])', { timeout: 30000 });
    const unused = await sender.$eval('[data-dm-media-draft]', element => element.dataset.dmMediaDraft.split('\n').at(-1));
    const unusedBase = await sender.evaluate(async id => {
        const { dmAssetContainer } = await import('./dm-attachments.js');
        const { solidSession } = await import('./auth.js');
        return dmAssetContainer(window.proxionCrossMode?.isReady() ? window.proxionCrossMode.state().webid : solidSession.info.webId) + 'asset-' + id;
    }, unused);
    const hit = await sender.$eval('[data-dm-media-cleanup]', button => {
        button.scrollIntoView({ block: 'center' });
        const rect = button.getBoundingClientRect(), target = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return { matches: target === button || button.contains(target), tag: target?.tagName, id: target?.id, className: target?.className };
    });
    if (!hit.matches) throw Error('Cleanup control is covered: ' + JSON.stringify(hit));
    await sender.click('[data-dm-media-cleanup]');
    await sender.waitForSelector('#confirm-modal', { visible: true });
    await sender.click('#confirm-ok');
    await sender.waitForFunction(() => document.querySelector('[data-dm-media-cleanup]:not([disabled])')?.textContent.includes('Retry cleanup'), { timeout: 30000 });
    if (await sender.$('[data-dm-media-retry]')) throw Error('Cleanup draft could still publish');
    await sender.reload({ waitUntil: 'networkidle2' }); await ready(sender); await openSender();
    await sender.waitForSelector('[data-dm-media-cleanup]:not([disabled])', { timeout: 20000 });
    await sender.click('[data-dm-media-cleanup]');
    await sender.waitForFunction(() => document.getElementById('dm-media-drafts').hidden, { timeout: 30000 });
    if (cleanupInterceptor) { await sender.setRequestInterception(false); sender.off('request', cleanupInterceptor); }
    const gone = await receiver.evaluate(async base => {
        const cross = window.proxionCrossMode?.isReady() ? window.proxionCrossMode : null;
        for (let index = 0; index < 4; index++) {
            const url = base + '-' + index + '.json';
            const response = cross ? await cross.bridge.fetch(url, { method: 'HEAD' }) : await fetch(url, { method: 'HEAD', credentials: 'omit' });
            if (response.status !== 404) return false;
        }
        return true;
    }, unusedBase);
    if (!gone) throw Error('Unused upload chunks remained after cleanup');
    const retained = await receiver.evaluate(async ({ id, text }) => {
        const { dmHistoryGet } = await import('./dmhistory.js');
        const { createDmAttachments } = await import('./dm-attachments.js');
        const { solidSession } = await import('./auth.js');
        const cross = window.proxionCrossMode?.isReady() ? window.proxionCrossMode : null;
        const record = await dmHistoryGet(id);
        const result = await createDmAttachments({ getSelfWebId: () => cross ? cross.state().webid : solidSession.info.webId,
            fetch: (url, options) => cross ? cross.bridge.fetch(url, options) : fetch(url, { ...options, credentials: 'omit' }) }).download(record.from_webid, record.content);
        return result.ok && new TextDecoder().decode(result.bytes) === text;
    }, { id, text });
    if (!retained) throw Error('Cleanup broke a delivered file');
    console.log(`  ✓ ${label}: interrupted cleanup resumes after reload, removes unused chunks and preserves delivered files`);

    // The recipient forwards a verified copy back to its sender. The copy must
    // now belong to the forwarder's pod, with a fresh encryption key and id.
    await openReceiver(); await receiver.bringToFront();
    const original = await receiver.evaluate(async id => {
        const { dmHistoryGet } = await import('./dmhistory.js');
        const { parseDmAttachment } = await import('./dm-attachments.js');
        const record = await dmHistoryGet(id);
        return { manifest: parseDmAttachment(record.content), threadId: record.thread_id };
    }, id);
    const forwardingDesktop = await receiver.evaluate(() => !!window.proxionCrossMode?.isReady());
    let forwardOutage = false, forwardInterceptor;
    if (forwardingDesktop) {
        await receiver.evaluate(() => {
            const bridge = window.proxionCrossMode.bridge, original = bridge.fetch; let failed = false;
            bridge.fetch = (url, options) => {
                if (!failed && options?.method === 'PUT' && url.includes('/dm-assets/asset-')) {
                    failed = true; return Promise.reject(Error('forward upload outage'));
                }
                return original(url, options);
            };
        });
    } else {
        await receiver.setRequestInterception(true);
        forwardInterceptor = request => {
            if (!forwardOutage && request.method() === 'PUT' && request.url().includes('/dm-assets/asset-')) {
                forwardOutage = true; request.abort('failed').catch(() => {});
            } else request.continue().catch(() => {});
        };
        receiver.on('request', forwardInterceptor);
    }
    async function chooseForward(sourceId) {
        await receiver.$$eval('.toast__close', buttons => buttons.forEach(button => button.click()));
        await receiver.click('#msg-' + sourceId, { button: 'right' });
        await receiver.waitForSelector('#ctx-forward', { visible: true }); await receiver.click('#ctx-forward');
        await receiver.waitForSelector('#forward-modal', { visible: true });
        const target = await receiver.evaluateHandle(thread => [...document.querySelectorAll('[data-forward-thread-id]')]
            .find(button => button.dataset.forwardThreadId === thread), original.threadId);
        const button = target.asElement(); if (!button) throw Error('DM forwarding destination missing');
        await button.click(); await target.dispose();
    }
    await chooseForward(id);
    await receiver.waitForSelector('[data-dm-media-retry]:not([disabled])', { timeout: 30000 });
    const copyId = await receiver.$eval('[data-dm-media-draft]', el => el.dataset.dmMediaDraft.split('\n').at(-1));
    if (forwardInterceptor) { await receiver.setRequestInterception(false); receiver.off('request', forwardInterceptor); }
    await receiver.reload({ waitUntil: 'networkidle2' }); await ready(receiver); await openReceiver();
    await receiver.waitForSelector('[data-dm-media-retry]:not([disabled])', { timeout: 20000 });
    await receiver.click('[data-dm-media-retry]');
    await receiver.waitForFunction(() => document.getElementById('dm-media-drafts').hidden, { timeout: 30000 });
    const fresh = await receiver.evaluate(async id => {
        const { dmHistoryGet } = await import('./dmhistory.js'); const { parseDmAttachment } = await import('./dm-attachments.js');
        return parseDmAttachment((await dmHistoryGet(id)).content);
    }, copyId);
    if (!fresh || ['id', 'key', 'iv', 'sha256'].some(field => fresh[field] === original.manifest[field])) throw Error('Forward reused source encryption');
    await openSender();
    await until(sender, id => !!document.querySelector(`#msg-${id} [data-dm-asset]:not([disabled])`), copyId);
    await sender.$$eval('.toast__close', buttons => buttons.forEach(button => button.click()));
    await sender.click(`#msg-${copyId} [data-dm-asset]`);
    await sender.waitForFunction(({ id, text }) => {
        const link = document.querySelector(`#msg-${id} a[download]`);
        return link && atob(link.href.split(',')[1] || '') === text;
    }, { timeout: 30000 }, { id: copyId, text });
    console.log(`  ✓ ${label}: private Forward uses a fresh key/id; failed upload survives reload and exact bytes arrive from the forwarder's pod`);

    await openReceiver();
    await until(receiver, () => !!document.querySelector('.audio-message'));
    const voiceSource = await receiver.$eval('.audio-message', element => element.closest('[id^="msg-"]').id.slice(4));
    const existing = await sender.$$eval('[data-dm-asset]', buttons => buttons.map(button => button.dataset.dmAsset));
    await chooseForward(voiceSource);
    await receiver.waitForFunction(() => document.getElementById('forward-modal').style.display === 'none', { timeout: 30000 });
    await until(sender, existing => [...document.querySelectorAll('[data-dm-asset]:not([disabled])')]
        .some(button => !existing.includes(button.dataset.dmAsset) && button.parentElement.textContent.includes('voice.webm')), existing);
    const voiceCopy = await sender.$$eval('[data-dm-asset]:not([disabled])', (buttons, existing) => buttons
        .find(button => !existing.includes(button.dataset.dmAsset) && button.parentElement.textContent.includes('voice.webm')).dataset.dmAsset, existing);
    await sender.$$eval('.toast__close', buttons => buttons.forEach(button => button.click()));
    await sender.click(`#msg-${voiceCopy} [data-dm-asset]`);
    await sender.waitForFunction(id => document.querySelector(`#msg-${id} audio`)?.readyState >= 1, { timeout: 20000 }, voiceCopy);
    await sender.$eval(`#msg-${voiceCopy} audio`, async audio => { await audio.play(); audio.pause(); });
    console.log(`  ✓ ${label}: a forwarded voice note decrypts and plays`);
}
