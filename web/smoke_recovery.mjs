// Round 130: real gateway recovery/export UI and denied microphone access.
// Always use throwaway state: this journey restores its own generated kit.
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import puppeteer from 'puppeteer-core';
import { smokeGateway } from './scripts/gw-harness.mjs';
import { skipSmoke } from './scripts/smoke-status.mjs';
if (process.env.PROXION_SMOKE_URL) throw Error('Recovery smoke requires its own throwaway gateway.');
const chrome = [process.env.PROXION_CHROME, '/usr/bin/chromium', '/usr/bin/google-chrome'].find(p => p && existsSync(p));
if (!chrome) skipSmoke('Set PROXION_CHROME.');
let gateway, browser, page, step = 'start';
const downloads = mkdtempSync(join(tmpdir(), 'proxion-recovery-'));
const wait = (fn, arg) => page.waitForFunction(fn, { polling: 100, timeout: 20000 }, arg);
const click = selector => page.evaluate(s => document.querySelector(s).click(), selector);
const ok = text => console.log('  ✓ ' + text);
async function downloaded(prefix) {
    for (let i = 0; i < 100; i++) {
        const name = readdirSync(downloads).find(n => n.startsWith(prefix) && n.endsWith('.json'));
        if (name) return readFileSync(join(downloads, name), 'utf8');
        await new Promise(r => setTimeout(r, 100));
    }
    throw Error('Download did not complete: ' + prefix);
}
async function attach(id, text) {
    await page.evaluate(({ id, text }) => {
        const input = document.getElementById(id), data = new DataTransfer();
        data.items.add(new File([text], 'recovery.json', { type: 'application/json' }));
        input.files = data.files; input.dispatchEvent(new Event('change', { bubbles: true }));
    }, { id, text });
}
async function prompt(text) {
    await page.waitForSelector('#prompt-modal', { visible: true });
    await page.type('#prompt-input', text); await click('#prompt-ok');
}
try {
    gateway = await smokeGateway();
    browser = await puppeteer.launch({ executablePath: chrome, headless: 'new', args: ['--no-sandbox', '--ignore-certificate-errors'] });
    page = await browser.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const cdp = await page.createCDPSession();
    await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });
    await page.evaluateOnNewDocument(() => {
        localStorage.setItem('proxion_display_name', 'Recovery Tester'); localStorage.setItem('proxion_pod_setup_skipped', '1');
    });
    await page.goto(gateway.url, { waitUntil: 'load' });
    await wait(() => document.querySelector('.dot')?.classList.contains('online'));
    await page.evaluate(() => { const el = document.getElementById('onboarding-modal'); if (el) el.style.display = 'none'; });
    step = 'fixture';
    step = 'fixture-create';
    await click('#create-room-btn'); await page.type('#room-name-input', 'recovery-audit'); await click('#room-create-submit');
    await wait(() => document.getElementById('room-invite-result')?.style.display !== 'none');
    step = 'fixture-open';
    await click('#room-create-done-btn');
    await wait(() => !document.getElementById('message-input').disabled);
    step = 'fixture-send';
    await new Promise(r => setTimeout(r, 500));
    await page.type('#message-input', 'round130-recovery-fixture'); await page.keyboard.press('Enter');
    await wait(() => document.getElementById('message-feed').innerText.includes('round130-recovery-fixture'));
    step = 'microphone-denied';
    await page.evaluate(() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Denied', 'NotAllowedError'); }; });
    await page.click('#voice-record-btn');
    await wait(() => document.getElementById('toast-container')?.innerText.includes('Microphone access denied'));
    if (await page.$eval('#voice-recording-bar', el => el.classList.contains('active'))) throw Error('Recorder stayed active after denial');
    ok('microphone denial leaves recording stopped and the conversation usable');
    step = 'backup';
    await click('#settings-btn'); await click('[data-settings-target="settings-sec-backup"]');
    await click('#settings-backup-btn'); await page.waitForSelector('#recovery-kit-modal', { visible: true });
    const code = await page.$eval('#recovery-code-display', el => el.textContent);
    if (!await page.$eval('#recovery-download-btn', el => el.disabled)) throw Error('Kit download skipped saved-code confirmation');
    await click('#recovery-confirm-saved'); await click('#recovery-download-btn');
    const kit = await downloaded('proxion-recovery-kit-'); JSON.parse(kit);
    ok('recovery kit downloads only after confirming the generated code was saved');
    step = 'verify-reject';
    await attach('settings-verify-input', kit);
    let response = page.waitForResponse(r => r.url().includes('/restore?dry_run=1'));
    await prompt('wrong-code-for-recovery');
    if ((await response).ok()) throw Error('Wrong recovery code was accepted');
    step = 'verify-valid';
    await attach('settings-verify-input', kit);
    response = page.waitForResponse(r => r.url().includes('/restore?dry_run=1'));
    await prompt(code);
    const verified = await response;
    if (!verified.ok() || !(await verified.json()).valid) throw Error('Recovery kit did not verify');
    ok('wrong codes fail and the saved kit/code verifies without replacing identity');
    step = 'export';
    const section = await page.$eval('#export-data-btn', el => el.closest('.settings-section').id);
    await click('[data-settings-target="' + section + '"]'); await click('#export-data-btn');
    const exported = await downloaded('proxion-export'); JSON.parse(exported);
    if (!exported.includes('round130-recovery-fixture')) throw Error('Export omitted the account message fixture');
    ok('account export downloads the saved conversation');
    step = 'restore';
    await click('[data-settings-target="settings-sec-backup"]');
    await attach('settings-restore-input', kit);
    response = page.waitForResponse(r => new URL(r.url()).pathname === '/restore' && !new URL(r.url()).search);
    await prompt(code);
    if (!(await response).ok()) throw Error('Restoring the throwaway kit failed');
    ok('the throwaway gateway restores its own recovery kit through the UI');
    if (errors.length) throw Error('Uncaught browser errors: ' + errors.join('; '));
    console.log('SMOKE PASSED');
 } catch (e) {
    console.error('  ✗ [' + step + '] ' + e.message);
    if (page && (step.startsWith('fixture') || step === 'microphone-denied')) console.error(await page.evaluate(() => ({
        header: document.getElementById('chat-header-name')?.textContent, draft: document.getElementById('message-input')?.value,
        disabled: document.getElementById('message-input')?.disabled, modal: document.getElementById('onboarding-modal')?.style.display,
        feed: document.getElementById('message-feed')?.innerText?.slice(-500), toast: document.getElementById('toast-container')?.innerText,
    })));
    process.exitCode = 1;
}
finally { await browser?.close(); await gateway?.stop(); rmSync(downloads, { recursive: true, force: true }); }
