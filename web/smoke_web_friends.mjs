// Two real browser accounts: mutual friendship and private request storage.
import { existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import puppeteer from 'puppeteer-core';
import { startCss, provisionAccount } from './scripts/css-harness.mjs';
import { buildAndServeHttps, signIn } from './scripts/e2e-signin.mjs';
import { skipSmoke } from './scripts/smoke-status.mjs';
const HERE = resolve(fileURLToPath(new URL('.', import.meta.url)));
const chrome = [process.env.PROXION_CHROME, '/usr/bin/chromium', '/usr/bin/google-chrome'].find(path => path && existsSync(path));
if (!chrome) skipSmoke('Set PROXION_CHROME to a Chromium executable.');
let browser, css, served, step = 'start';
const ok = text => console.log('  ✓ ' + text);
const wait = (page, fn, value, timeout = 45000) => page.waitForFunction(fn, { timeout, polling: 300 }, value);
const state = (page, peer, expected) => wait(page, ({ peer, expected }) => window.proxionWebFriends?.stateFor(peer).state === expected, { peer, expected });
const ready = page => wait(page, () => window.proxionWebFriends?.view().ready);
const action = (page, peer, name) => page.evaluate(({ peer, name }) => {
    const btn = [...document.querySelectorAll('[data-bf-action]')].find(el => el.dataset.peer === peer && el.dataset.bfAction === name);
    if (!btn || btn.disabled) throw Error('Missing enabled friend action ' + name);
    btn.click();
}, { peer, name });
async function request(page, peer) {
    await page.evaluate(() => document.getElementById('browser-add-friend-btn').click());
    await page.type('#add-peer-input', peer);
    await page.click('#add-peer-submit-btn');
}
async function reload(page) { await page.reload({ waitUntil: 'load' }); await ready(page); }
try {
    served = await buildAndServeHttps(HERE, join(HERE, 'scripts/build-web.mjs'));
    css = await startCss({ allowSelfSignedClientId: true });
    const stamp = Date.now();
    const alice = await provisionAccount(css.url, { email: `friend-alice-${stamp}@test.example`, password: 'pw-12345678', label: 'alice' });
    const bob = await provisionAccount(css.url, { email: `friend-bob-${stamp}@test.example`, password: 'pw-12345678', label: 'bob' });
    browser = await puppeteer.launch({ executablePath: chrome, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
    const a = await (await browser.createBrowserContext()).newPage(), b = await (await browser.createBrowserContext()).newPage();
    const errors = [];
    for (const page of [a, b]) page.on('pageerror', error => errors.push(error.message));
    await signIn(a, { appOrigin: served.origin, cssUrl: css.url, cookies: alice.cookies });
    await signIn(b, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies });
    await ready(a); await ready(b); ok('both private Friends lists loaded');
    const intent = `${alice.storageRoot}proxion/friends/peers/${createHash('sha256').update(bob.webId).digest('hex')}.json`;
    let rejectSave = true, rejectDelivery = true;
    await a.setRequestInterception(true);
    a.on('request', request => {
        if ((rejectSave && request.url() === intent && request.method() === 'PUT')
            || (rejectDelivery && request.url() === bob.storageRoot + 'proxion/friend-inbox/' && request.method() === 'POST')) request.abort('failed').catch(() => {});
        else request.continue().catch(() => {});
    });
    step = 'failed-save';
    await request(a, bob.webId);
    await wait(a, () => !!document.getElementById('add-peer-error')?.textContent && !document.getElementById('add-peer-submit-btn').disabled);
    if (await a.$eval('#add-peer-input', el => el.value) !== bob.webId) throw Error('Failed save lost the selected address');
    await state(a, bob.webId, 'none'); ok('a rejected save stays retryable and does not report friendship');
    rejectSave = false; await a.click('#add-peer-submit-btn');
    await a.waitForSelector('#add-peer-modal', { hidden: true }); await state(a, bob.webId, 'outgoing');
    await wait(a, peer => window.proxionWebFriends.stateFor(peer).queued, bob.webId);
    step = 'reload-pending';
    await reload(a); // interception survives: the persisted outbox must still be pending
    await state(a, bob.webId, 'outgoing');
    rejectDelivery = false; await action(a, bob.webId, 'retry');
    await state(b, alice.webId, 'incoming');
    await reload(b); await state(b, alice.webId, 'incoming');
    ok('pending requests and delivery retries survive reload without inbox-drain hooks');
    step = 'accept';
    await action(b, alice.webId, 'accept'); await state(a, bob.webId, 'friends'); await state(b, alice.webId, 'friends');
    await reload(a); await reload(b); await state(a, bob.webId, 'friends'); await state(b, alice.webId, 'friends');
    ok('Accept produces mutual Friends lists on both accounts after reload');
    step = 'privacy';
    const anonymous = await fetch(intent);
    if (![401,403].includes(anonymous.status)) throw Error('Friend intent is publicly readable: ' + anonymous.status);
    const privacy = await b.evaluate(async ({ intent, index }) => {
        const { solidSession } = await import('./auth.js');
        const read = await solidSession.fetch(intent);
        const write = await solidSession.fetch(intent, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const list = await solidSession.fetch(index);
        return { read: read.status, write: write.status, list: list.status };
    }, { intent, index: alice.storageRoot + 'proxion/friends/index.json' });
    if (privacy.read !== 200 || ![401,403].includes(privacy.write) || ![401,403].includes(privacy.list)) throw Error('Friend privacy/ownership: ' + JSON.stringify(privacy));
    ok('only the intended peer can read an intent; peers cannot write it or read the private index');
    step = 'message';
    await action(a, bob.webId, 'message');
    const hello = 'friends-hello-' + stamp;
    await a.type('#message-input', hello); await a.keyboard.press('Enter');
    await wait(b, peer => !!document.getElementById('nav-' + peer), alice.webId);
    await b.evaluate(peer => document.getElementById('nav-' + peer).click(), alice.webId);
    await wait(b, text => document.getElementById('message-feed')?.innerText.includes(text), hello);
    ok('a confirmed friend opens the existing encrypted DM path');
    step = 'remove';
    await action(a, bob.webId, 'remove'); await a.waitForSelector('#confirm-modal', { visible: true }); await a.click('#confirm-ok');
    await state(a, bob.webId, 'none'); await state(b, alice.webId, 'none');
    await reload(a); await reload(b); await state(a, bob.webId, 'none'); await state(b, alice.webId, 'none');
    ok('Remove revokes friendship on both accounts without reviving old requests');
    step = 'decline';
    await request(a, bob.webId); await state(b, alice.webId, 'incoming'); await action(b, alice.webId, 'decline');
    await state(a, bob.webId, 'none'); await state(b, alice.webId, 'none');
    ok('Decline settles both request lists');
    step = 'withdraw';
    await request(a, bob.webId); await state(b, alice.webId, 'incoming'); await action(a, bob.webId, 'withdraw');
    await state(a, bob.webId, 'none'); await state(b, alice.webId, 'none');
    await reload(a); await reload(b); await state(a, bob.webId, 'none'); await state(b, alice.webId, 'none');
    ok('Withdraw remains cancelled after both clients reload');
    if (errors.length) throw Error('Page errors: ' + errors.join('\n'));
    console.log('\nSMOKE PASSED');
} catch (error) { console.error(`  ✗ [${step}] ${error.stack || error}`); process.exitCode = 1; }
finally {
    try { await browser?.close(); } catch { /* cleanup */ }
    try { served?.server.close(); } catch { /* cleanup */ }
    try { css?.stop(); } catch { /* cleanup */ }
}
