// Two-party web DM E2E. Two real signed-in browser contexts (Alice, Bob) on a
// live CSS pod, no gateway: DMs travel as E2E envelopes dropped into the
// recipient's pod inbox. Checks the flows a unit test cannot cross:
//   - a DM received while its thread is closed renders when the thread opens
//   - the contact is labelled with the account name, not the pod's host
//   - a reply quote shows plaintext on both sides (never the ciphertext echo)
//   - both sides keep the thread (and its history) across a reload
//   - a block stops further DMs, and survives a reload
//   - attach / voice message / disappearing messages are not offered
//
//   node web/smoke_web_dm.mjs
// Needs Chrome (or PROXION_CHROME), openssl, and npx (for CSS). Self-skips if the
// cert or CSS cannot be produced.

import { existsSync } from 'fs';
import { resolve, join } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
import { startCss, provisionAccount } from './scripts/css-harness.mjs';
import { buildAndServeHttps, signIn } from './scripts/e2e-signin.mjs';

const HERE = resolve(fileURLToPath(new URL('.', import.meta.url)));
const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find((p) => p && existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

let step = 'init';
const fail = (m) => { console.error(`  \u2717 [${step}] ${m}`); process.exitCode = 1; };
const ok = (m) => console.log(`  \u2713 ${m}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let browser = null, served = null, css = null;
async function cleanup() {
  try { if (browser) await browser.close(); } catch { /**/ }
  try { if (served) served.server.close(); } catch { /**/ }
  try { if (css) css.stop(); } catch { /**/ }
}

// Pull pending drops now instead of waiting on pod-notification timing.
async function drainUntil(page, check, arg, tries = 14) {
  for (let i = 0; i < tries; i++) {
    try { await page.evaluate(() => window.proxionWebDm && window.proxionWebDm.drainOnce()); } catch { /**/ }
    if (await page.evaluate(check, arg).catch(() => false)) return true;
    await sleep(1200);
  }
  return false;
}

const navRow = (page, webid) => page.evaluate((w) => {
  const li = document.getElementById('nav-' + w);
  return li ? li.innerText : null;
}, webid);
const feedText = (page) => page.evaluate(() => document.getElementById('message-feed')?.innerText || '');
const openThread = (page, webid) => page.evaluate((w) => {
  document.getElementById('nav-' + w)?.querySelector('.dm-item-body')?.click();
}, webid);

// Type and send in the open thread; accept the "send unencrypted" prompt if the
// peer key is not published (the drop still has to land).
async function send(page, text) {
  await page.focus('#message-input');
  await page.keyboard.type(text);
  await page.keyboard.press('Enter');
  await sleep(600);
  const prompted = await page.evaluate(() => { const m = document.getElementById('confirm-modal'); return !!m && getComputedStyle(m).display !== 'none'; });
  if (prompted) await page.click('#confirm-ok');
  // Delivered: the optimistic bubble is confirmed by the pod echo.
  return page.waitForFunction((t) => Array.from(document.querySelectorAll('#message-feed [id^="msg-"]'))
    .some((el) => el.innerText.includes(t) && !el.classList.contains('msg-pending')), { timeout: 20000 }, text)
    .then(() => true).catch(() => false);
}

try {
  step = 'serve';
  served = await buildAndServeHttps(HERE, join(HERE, 'scripts', 'build-web.mjs'));
  if (!served) { console.log('  \u2013 skipped: could not build/serve over https (openssl?).'); await cleanup(); process.exit(0); }
  ok('built + serving the web app over https');

  step = 'css';
  css = await startCss({ allowSelfSignedClientId: true }).catch(() => null);
  if (!css) { console.log('  \u2013 skipped: no CSS pod available (offline / npx blocked).'); await cleanup(); process.exit(0); }
  const stamp = Date.now();
  const alice = await provisionAccount(css.url, { email: `alice-${stamp}@test.example`, password: 'pw-12345678', label: 'alice' });
  const bob = await provisionAccount(css.url, { email: `bob-${stamp}@test.example`, password: 'pw-12345678', label: 'bob' });
  ok('CSS pod up + two accounts provisioned');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  const pageA = await (await browser.createBrowserContext()).newPage();
  const pageB = await (await browser.createBrowserContext()).newPage();
  const errs = [];
  pageA.on('pageerror', (e) => errs.push('A: ' + e.message));
  pageB.on('pageerror', (e) => errs.push('B: ' + e.message));

  step = 'signin';
  await signIn(pageA, { appOrigin: served.origin, cssUrl: css.url, cookies: alice.cookies });
  await signIn(pageB, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies });
  await sleep(2500);   // both inboxes + E2E keys published
  ok('both accounts signed in');

  // ── Web-gated composer controls ──
  step = 'gating';
  const shown = await pageA.evaluate(() => ['attach-btn', 'voice-record-btn', 'gif-tray-btn']
    .filter((id) => { const el = document.getElementById(id); return el && getComputedStyle(el).display !== 'none'; }));
  if (!shown.length) ok('attach, GIF tray and voice-message controls are hidden');
  else fail('still shown in the web build: ' + shown.join(', '));

  // ── Alice adds Bob by WebID ──
  step = 'add';
  await pageA.evaluate(() => document.getElementById('add-peer-btn').click());
  await pageA.waitForSelector('#add-peer-input', { visible: true, timeout: 10000 });
  await pageA.type('#add-peer-input', bob.webId);
  await pageA.click('#add-peer-submit-btn');
  await pageA.waitForFunction((w) => !!document.getElementById('nav-' + w), { timeout: 15000 }, bob.webId);
  const rowA = await navRow(pageA, bob.webId);
  const bobAccount = bob.podUrl.replace(/\/$/, '').split('/').pop();
  if (rowA && rowA.includes(bobAccount) && !rowA.includes(new URL(css.url).host)) ok(`contact is labelled "${bobAccount}", not the pod host`);
  else fail(`contact row reads "${rowA}"`);
  const addLabel = await pageA.$eval('#add-peer-submit-btn', (b) => b.textContent.trim());
  if (!/request/i.test(addLabel)) ok('the Add button keeps its label');
  else fail(`Add button reads "${addLabel}"`);
  const disappear = await pageA.evaluate(() => { const el = document.getElementById('room-opt-disappear'); return !!el && !el.hidden; });
  if (!disappear) ok('disappearing messages are not offered in a web DM');
  else fail('disappearing messages offered in a web DM');

  step = 'send';
  if (await send(pageA, 'hello-1')) ok('Alice sent a DM, the drop landed');
  else fail('the first DM was never confirmed delivered');

  // ── Bob: thread closed, the DM arrives, then he opens it ──
  step = 'receive-closed';
  const aliceAccount = alice.podUrl.replace(/\/$/, '').split('/').pop();
  const listed = await drainUntil(pageB, (w) => !!document.getElementById('nav-' + w), alice.webId);
  if (listed) ok('the DM thread appeared in Bob\'s sidebar');
  else fail('the DM never reached Bob');
  await openThread(pageB, alice.webId);
  const shownB = await pageB.waitForFunction(() => (document.getElementById('message-feed')?.innerText || '').includes('hello-1'), { timeout: 10000 })
    .then(() => true).catch(() => false);
  if (shownB) ok('a DM received while the thread was closed renders on open');
  else fail('opening the thread showed: ' + JSON.stringify((await feedText(pageB)).slice(0, 200)));
  const rowB = await navRow(pageB, alice.webId);
  if (rowB && rowB.includes(aliceAccount)) ok('Bob sees Alice by her account name');
  else fail(`Bob's row for Alice reads "${rowB}"`);

  // ── Bob replies to Alice's message ──
  step = 'reply';
  await pageB.evaluate(() => {
    const el = Array.from(document.querySelectorAll('#message-feed [id^="msg-"]')).find((e) => e.innerText.includes('hello-1'));
    el?.querySelector('[data-msg-action="reply"]')?.click();
  });
  if (await send(pageB, 'reply-1')) ok('Bob replied');
  else fail('the reply was never confirmed delivered');
  const quoteOf = (page) => page.evaluate(() => {
    const el = Array.from(document.querySelectorAll('#message-feed [id^="msg-"]')).find((e) => e.innerText.includes('reply-1'));
    return el?.querySelector('.reply-context')?.innerText || null;
  });
  const qB = await quoteOf(pageB);
  if (qB && qB.includes('hello-1')) ok('Bob\'s reply quote shows the plaintext');
  else fail(`Bob's reply quote reads ${JSON.stringify(qB)}`);
  // Alice has the thread open; her own message must keep its plaintext.
  const gotReply = await drainUntil(pageA, () => (document.getElementById('message-feed')?.innerText || '').includes('reply-1'));
  const qA = gotReply ? await quoteOf(pageA) : null;
  if (qA && qA.includes('hello-1')) ok('Alice sees the reply quoting her message in plaintext');
  else fail(`Alice's reply quote reads ${JSON.stringify(qA)}`);

  // ── Reload both: the thread and its history stay ──
  step = 'reload';
  await Promise.all([pageA.reload({ waitUntil: 'networkidle2', timeout: 30000 }), pageB.reload({ waitUntil: 'networkidle2', timeout: 30000 })]);
  const kept = async (page, w) => page.waitForFunction((x) => !!document.getElementById('nav-' + x), { timeout: 20000 }, w).then(() => true).catch(() => false);
  if (await kept(pageA, bob.webId)) ok('Alice keeps the thread after a reload');
  else fail('Alice lost the thread on reload');
  if (await kept(pageB, alice.webId)) ok('Bob keeps the thread after a reload');
  else fail('Bob lost the thread on reload');
  await openThread(pageB, alice.webId);
  const hist = await pageB.waitForFunction(() => { const t = document.getElementById('message-feed')?.innerText || ''; return t.includes('hello-1') && t.includes('reply-1'); }, { timeout: 10000 })
    .then(() => true).catch(() => false);
  if (hist) ok('the reopened thread shows its history after a reload');
  else fail('history after reload: ' + JSON.stringify((await feedText(pageB)).slice(0, 200)));

  // ── Bob blocks Alice: her next DMs never show ──
  step = 'block';
  await pageB.evaluate((w) => {
    const li = document.getElementById('nav-' + w);
    li.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 50, clientY: 50 }));
  }, alice.webId);
  await pageB.waitForSelector('#sctx-block:not([hidden])', { timeout: 5000 });
  await pageB.click('#sctx-block');
  await sleep(300);
  await openThread(pageA, bob.webId);
  await sleep(500);
  await send(pageA, 'blocked-1');
  await sleep(1000);
  const leaked = await drainUntil(pageB, () => (document.getElementById('message-feed')?.innerText || '').includes('blocked-1'), undefined, 4);
  if (!leaked) ok('a blocked contact\'s DM is not shown');
  else fail('a DM from a blocked contact was shown');
  await pageB.reload({ waitUntil: 'networkidle2', timeout: 30000 });
  await pageB.waitForFunction(() => typeof window.proxionWebDm === 'object', { timeout: 20000 });
  await sleep(1500);
  await pageB.evaluate(() => document.getElementById('settings-btn').click());
  const listedBlock = await pageB.waitForFunction((w) => (document.getElementById('settings-blocked-list')?.innerText || '').includes(w), { timeout: 8000 }, alice.webId)
    .then(() => true).catch(() => false);
  if (listedBlock) ok('the block survives a reload and shows in Settings');
  else fail('Settings blocked list: ' + JSON.stringify(await pageB.evaluate(() => document.getElementById('settings-blocked-list')?.innerText || '')));
  await pageB.keyboard.press('Escape');

  if (errs.length) fail('page errors:\n    ' + errs.slice(0, 6).join('\n    '));
  else ok('no uncaught page errors on either side');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
