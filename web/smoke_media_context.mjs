// Real gateway UI: attachment reads and recordings stay with their original
// conversation, and successful file/voice messages survive reload.
import { existsSync } from 'node:fs';
import puppeteer from 'puppeteer-core';
import { smokeGateway } from './scripts/gw-harness.mjs';
import { skipSmoke } from './scripts/smoke-status.mjs';
const chrome = [process.env.PROXION_CHROME, '/usr/bin/chromium', '/usr/bin/google-chrome'].find(path => path && existsSync(path));
if (!chrome) skipSmoke('Set PROXION_CHROME to a Chromium executable.');
let gateway, browser, page, step = 'start';
const wire = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const wait = (fn, arg) => page.waitForFunction(fn, { polling: 100, timeout: 20000 }, arg);
const click = selector => page.evaluate(selector => document.querySelector(selector).click(), selector);
const ok = message => console.log('  ✓ ' + message);
async function open(id) {
    await page.evaluate(id => document.getElementById('nav-' + id).querySelector('.room-item-body').click(), id);
    await wait(id => document.getElementById('nav-' + id)?.classList.contains('active'), id);
    await sleep(250);
}
async function create(name) {
    await click('#create-room-btn'); await page.type('#room-name-input', name); await click('#room-create-submit');
    await wait(() => document.getElementById('room-invite-result')?.style.display !== 'none');
    await click('#room-create-done-btn');
    await wait(name => [...document.querySelectorAll('#room-list li')].some(row => row.textContent.includes(name)), name);
    const id = await page.evaluate(name => [...document.querySelectorAll('#room-list li')].find(row => row.textContent.includes(name)).id.slice(4), name);
    // room_created schedules opening the new room. Finish that navigation
    // before the fixture explicitly chooses the attachment destination.
    await wait(id => document.getElementById('nav-' + id)?.classList.contains('active'), id);
    return id;
}
async function file(name) {
    await page.evaluate(name => {
        const transfer = new DataTransfer(); transfer.items.add(new File(['attachment test'], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
    }, name);
    await page.waitForSelector('#file-confirm-modal', { visible: true }); await click('#file-confirm-ok');
}
try {
    gateway = await smokeGateway();
    browser = await puppeteer.launch({ executablePath: chrome, headless: 'new', args: ['--no-sandbox', '--ignore-certificate-errors',
        '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
    page = await (await browser.createBrowserContext()).newPage(); const errors = [];
    const cdp = await page.createCDPSession(); await cdp.send('Network.enable');
    cdp.on('Network.webSocketFrameSent', event => {
        try { const message = JSON.parse(event.response.payloadData);
            if (['send_file', 'send_voice_message', 'get_local_history'].includes(message.cmd)) wire.push({ cmd: message.cmd, room: message.room_id, thread: message.thread_id });
        } catch { /* unrelated socket data */ }
    });
    page.on('pageerror', error => errors.push(error.message));
    await page.evaluateOnNewDocument(() => {
        localStorage.setItem('proxion_display_name', 'Media Tester'); localStorage.setItem('proxion_pod_setup_skipped', '1');
    });
    await page.goto(gateway.url, { waitUntil: 'load' });
    await wait(() => document.querySelector('.dot')?.classList.contains('online'));
    await page.evaluate(() => { const modal = document.getElementById('onboarding-modal'); if (modal) modal.style.display = 'none'; });
    const first = await create('media-first'), second = await create('media-second');
    await open(first);
    step = 'delayed-file';
    await page.evaluate(() => {
        const original = FileReader.prototype.readAsDataURL;
        FileReader.prototype.readAsDataURL = function(blob) {
            if (blob.name === 'delayed.txt') window.resumeMediaRead = () => original.call(this, blob);
            else original.call(this, blob);
        };
    });
    await file('delayed.txt'); await wait(() => typeof window.resumeMediaRead === 'function');
    await open(second); await page.evaluate(() => window.resumeMediaRead()); await sleep(500);
    if (await page.evaluate(() => [...document.querySelectorAll('#message-feed .attachment')].some(el => el.textContent.includes('delayed.txt')))) throw Error('Delayed attachment moved to the new room');
    await open(first);
    if (await page.evaluate(() => [...document.querySelectorAll('#message-feed .attachment')].some(el => el.textContent.includes('delayed.txt')))) throw Error('Cancelled attachment was sent');
    ok('a delayed file read is cancelled when its original conversation changes');
    step = 'normal-file'; await file('confirmed.txt');
    await wait(() => !!document.querySelector('#message-feed .attachment a[download="confirmed.txt"]'));
    ok('a confirmed attachment sends to the selected room');
    step = 'changed-recording';
    await page.evaluate(() => document.getElementById('voice-record-btn').dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
    await wait(() => document.getElementById('voice-recording-bar')?.classList.contains('active'));
    await sleep(700); await open(second);
    await page.evaluate(() => document.getElementById('voice-record-btn').dispatchEvent(new MouseEvent('mouseup', { bubbles: true })));
    await sleep(500);
    if (await page.evaluate(() => !!document.querySelector('#message-feed .audio-message'))) throw Error('Recording moved to the new room');
    await open(first);
    if (await page.evaluate(() => !!document.querySelector('#message-feed .audio-message'))) throw Error('Changed-conversation recording was sent');
    ok('a recording cannot move into another room');
    step = 'normal-recording'; await page.click('#voice-record-btn');
    await wait(() => document.getElementById('voice-recording-bar')?.classList.contains('active'));
    await sleep(800); await page.click('#voice-record-btn');
    await wait(() => !!document.querySelector('#message-feed .audio-message audio'));
    ok('a normal voice message sends with real recorded audio');
    step = 'reload'; await page.reload({ waitUntil: 'load' });
    await wait(() => document.querySelector('.dot')?.classList.contains('online')); await open(first);
    await wait(() => document.querySelector('#message-feed .audio-message audio')?.readyState >= 1
        && document.querySelector('#message-feed .attachment a[download="confirmed.txt"]')?.href
            === 'data:application/octet-stream;base64,YXR0YWNobWVudCB0ZXN0');
    ok('file and voice history survive reload');
    if (errors.length) throw Error('Uncaught page errors: ' + errors.join('; '));
    console.log('\nSMOKE PASSED');
} catch (error) {
    console.error(`  ✗ [${step}] ${error.stack || error}`);
    if (page) console.error(JSON.stringify({ wire, ui: await page.evaluate(() => ({
        active: [...document.querySelectorAll('#room-list li.active')].map(el => el.id),
        header: document.getElementById('chat-header-name')?.innerText, feed: document.getElementById('message-feed')?.innerText,
    })) }));
    process.exitCode = 1;
} finally {
    if (browser) await browser.close(); if (gateway) await gateway.stop();
}
