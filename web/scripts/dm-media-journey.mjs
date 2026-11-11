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
    await sender.reload({ waitUntil: 'networkidle2' }); await ready(sender); await openSender();
    await sender.waitForSelector('[data-dm-media-retry]:not([disabled])', { timeout: 20000 });
    await sender.click('[data-dm-media-retry]');
    await sender.waitForFunction(() => document.getElementById('dm-media-drafts').hidden, { timeout: 30000 });
    if (intercept) { await sender.setRequestInterception(false); sender.off('request', intercept); }
    await until(receiver, id => !!document.getElementById('msg-' + id)?.querySelector('[data-dm-asset]:not([disabled])'), id);
    await receiver.click(`#msg-${id} [data-dm-asset]`);
    const checkBytes = ({ name, text }) => document.querySelector(`a[download="${name}"]`)?.href === 'data:application/octet-stream;base64,' + btoa(text);
    await receiver.waitForFunction(checkBytes, { timeout: 20000 }, { name, text });
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
}
