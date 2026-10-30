// Session and connectivity in the browser build: two real accounts on a
// throwaway CSS pod. Covers a lost network (banner, an undelivered DM keeps its
// state across a thread switch and goes out when the network is back), a DM
// whose drop fails and is resent after a reload, a pod that answers 401 (banner,
// queued room write flushed once the pod works again), and a session that ends
// (sign-in-again banner, the room message queued and written after signing in,
// the draft kept). Also: cancelling consent at the provider is explained.
//
//   node web/smoke_web_session.mjs
// Needs Chrome (or PROXION_CHROME), openssl, and npx (for CSS). Self-skips if the
// cert or CSS cannot be produced.

import { existsSync } from 'fs';
import { resolve, join } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
import { startCss, provisionAccount } from './scripts/css-harness.mjs';
import { buildAndServeHttps, signIn, settleName } from './scripts/e2e-signin.mjs';

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

const promptOpen = () => { const m = document.getElementById('confirm-modal'); return !!m && getComputedStyle(m).display !== 'none'; };
async function waitFor(page, fn, arg, ms) {
  return page.waitForFunction(fn, { timeout: ms, polling: 500 }, arg).then(() => true).catch(() => false);
}
async function openNav(page, id) {
  await page.evaluate((w) => {
    const el = document.getElementById('nav-' + w);
    (el?.querySelector('.dm-item-body') || el)?.click();
  }, id);
  await waitFor(page, (w) => window.proxionTransport && document.getElementById('nav-' + w)?.classList.contains('active'), id, 5000);
  await sleep(800);
}
async function send(page, text) {
  await page.focus('#message-input');
  await page.keyboard.type(text);
  await page.keyboard.press('Enter');
  await sleep(700);
  if (await page.evaluate(promptOpen)) await page.click('#confirm-ok');
}
// The send state a bubble shows: its classes and notes.
const bubble = (page, text) => page.evaluate((t) => {
  const el = Array.from(document.querySelectorAll('#message-feed [id^="msg-"]')).find((e) => e.innerText.includes(t));
  if (!el) return null;
  return {
    pending: el.classList.contains('msg-pending'), failed: el.classList.contains('msg-failed'),
    pod: !!el.querySelector('.msg-pod-note'),
  };
}, text);
const banner = (page) => page.evaluate(() => {
  const b = document.getElementById('conn-banner');
  if (!b || getComputedStyle(b).display === 'none') return null;
  return { text: document.getElementById('conn-banner-text')?.textContent || '', btn: document.getElementById('conn-retry-btn')?.textContent || '' };
});
const queueCount = (page) => page.evaluate(async () => (await import('./podqueue.js')).podQueueCount());
async function bobHas(pageB, aliceWebId, text) {
  for (let i = 0; i < 10; i++) {
    await pageB.evaluate(() => window.proxionWebDm?.drainOnce?.()).catch(() => {});
    await sleep(1500);
    await openNav(pageB, aliceWebId);
    if (await pageB.evaluate((t) => (document.getElementById('message-feed')?.innerText || '').includes(t), text)) return true;
  }
  return false;
}

try {
  step = 'serve';
  served = await buildAndServeHttps(HERE, join(HERE, 'scripts', 'build-web.mjs'));
  if (!served) { console.log('  \u2013 skipped: could not build/serve over https (openssl?).'); await cleanup(); process.exit(0); }
  ok('built + serving the web app over https');

  step = 'css';
  css = await startCss({ allowSelfSignedClientId: true }).catch(() => null);
  if (!css) { console.log('  \u2013 skipped: no CSS pod available (offline / npx blocked).'); await cleanup(); process.exit(0); }
  const cssHost = new URL(css.url).host;
  const appOrigin = new URL(served.origin).origin;
  const stamp = Date.now();
  const alice = await provisionAccount(css.url, { email: `alice-${stamp}@test.example`, password: 'pw-12345678', label: 'alice' });
  const bob = await provisionAccount(css.url, { email: `bob-${stamp}@test.example`, password: 'pw-12345678', label: 'bob' });
  const bobRoot = bob.webId.split('/profile/')[0] + '/';
  ok('CSS pod up + two accounts provisioned');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  const pageA = await (await browser.createBrowserContext()).newPage();
  const pageB = await (await browser.createBrowserContext()).newPage();
  const errs = [];
  for (const [tag, p] of [['A', pageA], ['B', pageB]]) p.on('pageerror', (e) => errs.push(`${tag}: ${e.message}`));

  // ── Cancelling consent at the identity provider says so on the sign-in screen ──
  step = 'cancel-consent';
  const pageX = await (await browser.createBrowserContext()).newPage();
  for (const [n, v] of Object.entries(alice.cookies)) await pageX.setCookie({ name: n, value: v, domain: new URL(css.url).hostname, path: '/' });
  await pageX.goto(served.origin + '?mode=web', { waitUntil: 'networkidle2', timeout: 30000 });
  await sleep(500);
  await pageX.evaluate((u) => { window.proxionWebLogin(u); }, css.url).catch(() => {});
  await pageX.waitForSelector('#cancel', { timeout: 25000 });
  await Promise.all([pageX.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => null), pageX.click('#cancel')]);
  if (await waitFor(pageX, () => /cancel/i.test(document.getElementById('web-signin-error')?.textContent || ''), null, 15000)) ok('a cancelled consent is explained on the sign-in screen');
  else fail(`no message after cancelling consent: ${JSON.stringify(await pageX.evaluate(() => document.getElementById('web-signin-error')?.textContent))}`);
  await pageX.close();

  step = 'signin';
  await signIn(pageA, { appOrigin: served.origin, cssUrl: css.url, cookies: alice.cookies, name: 'Alice' });
  await signIn(pageB, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies, name: 'Bob' });
  ok('both accounts signed in');

  step = 'setup';
  await pageA.evaluate(() => document.getElementById('create-room-btn').click());
  await pageA.waitForSelector('#room-name-input', { visible: true });
  await pageA.type('#room-name-input', 'session-room');
  await pageA.click('#room-create-submit');
  await pageA.waitForFunction(() => (document.getElementById('room-invite-url')?.textContent || '').includes('?join='), { timeout: 15000 });
  const invite = await pageA.$eval('#room-invite-url', (el) => el.textContent.trim());
  const roomId = decodeURIComponent(new URL(invite).searchParams.get('join').split('~')[0]);
  await pageA.keyboard.press('Escape');
  await sleep(1500);
  await pageA.evaluate(() => document.getElementById('add-peer-btn').click());
  await pageA.waitForSelector('#add-peer-input', { visible: true });
  await pageA.type('#add-peer-input', bob.webId);
  await pageA.click('#add-peer-submit-btn');
  await pageA.waitForFunction((w) => !!document.getElementById('nav-' + w), { timeout: 15000 }, bob.webId);
  await openNav(pageA, bob.webId);
  await send(pageA, 'baseline-dm');
  if (await bobHas(pageB, alice.webId, 'baseline-dm')) ok('baseline DM delivered');
  else fail('the baseline DM never reached Bob');

  // ── Offline: banner, DM state across a thread switch, sent when back ──
  step = 'offline';
  await pageA.setOfflineMode(true);
  if (await waitFor(pageA, () => getComputedStyle(document.getElementById('conn-banner')).display !== 'none', null, 5000)) ok('an offline banner shows');
  else fail('no banner while offline');
  await openNav(pageA, bob.webId);
  await send(pageA, 'off-dm-1');
  await openNav(pageA, roomId);
  await openNav(pageA, bob.webId);
  const offBubble = await bubble(pageA, 'off-dm-1');
  if (offBubble && (offBubble.pending || offBubble.failed)) ok('the undelivered DM still shows as not delivered after a thread switch');
  else fail(`the offline DM looks sent after a thread switch: ${JSON.stringify(offBubble)}`);
  await pageA.setOfflineMode(false);
  if (await waitFor(pageA, () => getComputedStyle(document.getElementById('conn-banner')).display === 'none', null, 15000)) ok('the banner clears when back online');
  else fail(`the banner stayed after going back online: ${JSON.stringify(await banner(pageA))}`);
  if (await bobHas(pageB, alice.webId, 'off-dm-1')) ok('the DM sent while offline reached Bob once back online');
  else fail('the DM sent while offline never reached Bob');
  await openNav(pageA, bob.webId);
  const backBubble = await bubble(pageA, 'off-dm-1');
  if (backBubble && !backBubble.pending && !backBubble.failed) ok('and its bubble is confirmed');
  else fail(`the delivered DM still shows a send state: ${JSON.stringify(backBubble)}`);

  // ── A DM whose drop fails survives a reload and is resent ──
  step = 'dm-reload';
  await pageA.setRequestInterception(true);
  const failDrops = (req) => {
    const m = req.method();
    if (req.url().startsWith(bobRoot) && (m === 'PUT' || m === 'POST' || m === 'PATCH')) {
      req.respond({ status: 500, headers: { 'Access-Control-Allow-Origin': appOrigin }, body: 'down' });
    } else req.continue();
  };
  pageA.on('request', failDrops);
  await send(pageA, 'drop-fail-dm');
  await sleep(2500);
  pageA.off('request', failDrops);
  await pageA.setRequestInterception(false);
  const stored = await pageA.evaluate(async () => (await import('./dmhistory.js')).dmHistoryUndelivered().then((r) => r.map((x) => x.content)));
  if (stored.includes('drop-fail-dm')) ok('the failed DM is flagged undelivered in its record');
  else fail(`no undelivered flag on the failed DM: ${JSON.stringify(stored)}`);
  await pageA.reload({ waitUntil: 'networkidle2' });
  await pageA.waitForFunction(() => typeof window.proxionWebDm === 'object', { timeout: 30000 });
  if (await bobHas(pageB, alice.webId, 'drop-fail-dm')) ok('after a reload the undelivered DM was resent and reached Bob');
  else fail('the undelivered DM was not resent after a reload');

  // ── Pod answers 401: banner, room write queued, flushed when it works again ──
  step = 'pod-401';
  await openNav(pageA, roomId);
  await pageA.setRequestInterception(true);
  const deny = (req) => {
    const u = new URL(req.url());
    if (u.host === cssHost && /^DPoP /i.test(req.headers()['authorization'] || '') && req.method() !== 'OPTIONS') {
      req.respond({ status: 401, headers: { 'WWW-Authenticate': 'DPoP error="invalid_token"', 'Access-Control-Allow-Origin': appOrigin, 'Access-Control-Expose-Headers': 'WWW-Authenticate' }, body: 'expired' });
    } else req.continue();
  };
  pageA.on('request', deny);
  await send(pageA, 'exp-room-1');
  if (await waitFor(pageA, () => /sign in again/i.test(document.getElementById('conn-banner-text')?.textContent || '')
      && getComputedStyle(document.getElementById('conn-banner')).display !== 'none', null, 15000)) ok('repeated 401s show the sign-in-again banner');
  else fail(`no banner on 401s: ${JSON.stringify(await banner(pageA))}`);
  const expBubble = await bubble(pageA, 'exp-room-1');
  if (expBubble && expBubble.pod) ok('the room message shows "Not sent"');
  else fail(`the room message has no not-sent note: ${JSON.stringify(expBubble)}`);
  if ((await queueCount(pageA)) >= 1) ok('and it is in the durable queue');
  else fail('the room write was not queued');
  pageA.off('request', deny);
  await pageA.setRequestInterception(false);
  if (await waitFor(pageA, async () => (await import('./podqueue.js')).podQueueCount().then((n) => n === 0), null, 30000)) ok('once the pod works again the queued write is flushed');
  else fail('the queued room write was not flushed after the pod recovered');
  if (await waitFor(pageA, () => getComputedStyle(document.getElementById('conn-banner')).display === 'none', null, 10000)) ok('and the banner clears');
  else fail(`the banner stayed after recovery: ${JSON.stringify(await banner(pageA))}`);
  const flushed = await bubble(pageA, 'exp-room-1');
  if (flushed && !flushed.pod) ok('and the not-sent note is gone');
  else fail(`the note stayed after the flush: ${JSON.stringify(flushed)}`);

  // ── The session ends: banner, queued write, written after signing in again ──
  step = 'session-expired';
  await pageA.evaluate(async () => { (await import('./auth.js')).solidSession.events.emit('sessionExpired'); });
  const exp = await waitFor(pageA, () => /sign in again/i.test(document.getElementById('conn-retry-btn')?.textContent || '')
      && getComputedStyle(document.getElementById('conn-banner')).display !== 'none', null, 5000);
  if (exp) ok('an ended session shows a Sign in again banner');
  else fail(`no sign-in banner after sessionExpired: ${JSON.stringify(await banner(pageA))}`);
  await send(pageA, 'se-room-1');
  await sleep(2000);
  const seBubble = await bubble(pageA, 'se-room-1');
  if (seBubble && seBubble.pod) ok('a room message sent while signed out shows "Not sent"');
  else fail(`the signed-out room message looks sent: ${JSON.stringify(seBubble)}`);
  if ((await queueCount(pageA)) >= 1) ok('and it is queued');
  else fail('the signed-out room message was not queued');
  const nsDb = await pageA.evaluate(async () => (await import('./auth.js')).accountDbName('proxion-dm-history'));
  if (nsDb.includes('::')) ok('local stores stay on the account database');
  else fail(`stores fell back to the bare database: ${nsDb}`);
  await pageA.focus('#message-input');
  await pageA.keyboard.type('draft-kept');
  await Promise.all([
    pageA.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => null),
    pageA.click('#conn-retry-btn'),
  ]);
  if (await pageA.$('input[name="webId"]')) {
    await pageA.click('input[name="webId"]');
    await Promise.all([
      pageA.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => null),
      pageA.click('#authorize'),
    ]);
  }
  await pageA.waitForFunction(() => !location.href.includes('/consent') && !location.href.includes('/.oidc/'), { timeout: 30000 });
  await pageA.waitForFunction(() => typeof window.proxionWebDm === 'object', { timeout: 30000 });
  await settleName(pageA, '');
  if (await waitFor(pageA, async () => (await import('./podqueue.js')).podQueueCount().then((n) => n === 0), null, 20000)) ok('after signing in again the queued write went out');
  else fail('the queued room write is still waiting after signing in again');
  if (await waitFor(pageA, () => document.getElementById('message-input')?.value === 'draft-kept', null, 15000)) ok('the unsent draft came back after signing in');
  else fail('the draft was lost across sign-in: ' + JSON.stringify(await pageA.evaluate(() => ({ ss: sessionStorage.getItem('proxion_reauth_draft'), v: document.getElementById('message-input')?.value, nav: document.querySelector('#sidebar .active')?.id || null }))));
  await openNav(pageA, roomId);
  if (await waitFor(pageA, () => (document.getElementById('message-feed')?.innerText || '').includes('se-room-1'), null, 15000)) ok('the room shows the message from the pod');
  else fail('the message sent while signed out is not in the room');

  if (errs.length) fail('page errors:\n    ' + errs.slice(0, 6).join('\n    '));
  else ok('no uncaught page errors');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
