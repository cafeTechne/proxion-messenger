// Cross-mode DM smoke: Proxion desktop (a gateway with a pod) and the Proxion
// browser build messaging each other through their pods.
//
// A throwaway CSS holds two accounts. Alice runs the desktop client: a gateway
// connected to her pod (only inside this smoke, pointed at the throwaway CSS).
// Bob runs the browser build, signed in to his pod. Checks:
//   - Bob adds Alice by WebID and sends; Alice gets a message request, accepts,
//     and reads the plaintext, marked verified
//   - Alice replies through her gateway's pod bridge; Bob reads it
//   - every drop either side wrote is ciphertext, and Alice's inbox is drained
//   - a forged, unsigned drop "from Bob" is not opened
//   - presence flows both ways
//   - Alice blocks Bob; his next DM is deleted unread
//
//   node web/smoke_cross_mode_dm.mjs
// Needs Chrome (or PROXION_CHROME), python, openssl, and npx (for CSS). Self-skips
// if the cert or CSS cannot be produced.

import { existsSync, mkdtempSync } from 'fs';
import { spawn } from 'child_process';
import { tmpdir } from 'os';
import { resolve, join } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
import { skipSmoke } from './scripts/smoke-status.mjs';
import { startCss, provisionAccount, makeAuthSession, freePort } from './scripts/css-harness.mjs';
import { buildAndServeHttps, signIn } from './scripts/e2e-signin.mjs';

const HERE = resolve(fileURLToPath(new URL('.', import.meta.url)));
const REPO = resolve(HERE, '..');
const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find((p) => p && existsSync(p));
if (!CHROME) skipSmoke('No Chrome/Edge found; set PROXION_CHROME.');

let step = 'init';
const fail = (m) => { console.error(`  \u2717 [${step}] ${m}`); process.exitCode = 1; };
const ok = (m) => console.log(`  \u2713 ${m}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let browser = null, served = null, css = null, gw = null;
async function cleanup() {
  try { if (browser) await browser.close(); } catch { /**/ }
  try { if (served) served.server.close(); } catch { /**/ }
  try { if (gw) gw.kill(); } catch { /**/ }
  try { if (css) css.stop(); } catch { /**/ }
}

// Alice's desktop gateway, connected to HER pod on the throwaway CSS.
async function startGateway(cssUrl, acct) {
  const httpPort = await freePort(), wsPort = await freePort();
  const dataDir = mkdtempSync(join(tmpdir(), 'proxion-xmode-'));
  const env = {
    ...process.env,
    PROXION_DATA_DIR: dataDir, PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
    PROXION_HOST: '127.0.0.1', PROXION_PUBLIC_URL: '', PROXION_REQUIRE_AUTH: '0',
    PROXION_CSS_URL: cssUrl, PROXION_CSS_EMAIL: acct.email, PROXION_CSS_PASSWORD: acct.password,
    PROXION_ALLOW_PRIVATE_RELAY: '1', PROXION_WEB_DIR: HERE,
    PYTHONPATH: join(REPO, 'proxion-messenger-core', 'src'),
  };
  const p = spawn('python', ['scripts/run_test_gateway.py'], { cwd: REPO, env });
  let log = '';
  p._log = () => log;
  p.stdout.on('data', (d) => { log += d; });
  p.stderr.on('data', (d) => { log += d; });
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline && !/PROXION_GATEWAY_READY/.test(log)) {
    if (p.exitCode !== null) throw new Error('gateway exited:\n' + log.slice(-800));
    await sleep(300);
  }
  gw = p;
  const url = `http://127.0.0.1:${httpPort}/`;
  // The pod connects before the HTTP server binds; wait for it.
  for (let i = 0; i < 120; i++) {
    if (p.exitCode !== null) throw new Error('gateway exited:\n' + log.slice(-800));
    if (await fetch(url).then((r) => r.ok).catch(() => false)) return { url };
    await sleep(500);
  }
  throw new Error('gateway HTTP never came up:\n' + log.slice(-800));
}

const feedText = (page) => page.evaluate(() => document.getElementById('message-feed')?.innerText || '');
const openThread = (page, webid) => page.evaluate((w) => {
  document.getElementById('nav-' + w)?.querySelector('.dm-item-body')?.click();
}, webid);

async function send(page, text) {
  await page.focus('#message-input');
  await page.keyboard.type(text);
  await page.keyboard.press('Enter');
  await sleep(600);
  const prompted = await page.evaluate(() => { const m = document.getElementById('confirm-modal'); return !!m && getComputedStyle(m).display !== 'none'; });
  if (prompted) return 'prompted';   // a key was missing: never send plaintext here
  return page.waitForFunction((t) => Array.from(document.querySelectorAll('#message-feed [id^="msg-"]'))
    .some((el) => el.innerText.includes(t) && !el.classList.contains('msg-pending')), { timeout: 25000 }, text)
    .then(() => true).catch(() => false);
}

const waitFor = (page, fn, arg, timeout = 30000) => page.waitForFunction(fn, { timeout, polling: 500 }, arg)
  .then(() => true).catch(() => false);

try {
  step = 'serve';
  served = await buildAndServeHttps(HERE, join(HERE, 'scripts', 'build-web.mjs'));
  if (!served) { await cleanup(); skipSmoke('could not build/serve over https (openssl?).'); }

  step = 'css';
  css = await startCss({ allowSelfSignedClientId: true }).catch(() => null);
  if (!css) { await cleanup(); skipSmoke('no CSS pod available (offline / npx blocked).'); }
  const stamp = Date.now();
  const alice = await provisionAccount(css.url, { email: `alice-${stamp}@test.example`, password: 'pw-12345678', label: 'alice' });
  const bob = await provisionAccount(css.url, { email: `bob-${stamp}@test.example`, password: 'pw-12345678', label: 'bob' });
  const aliceSession = await makeAuthSession(alice);
  ok('CSS up, Alice (desktop) and Bob (browser build) provisioned');

  step = 'gateway';
  const A = await startGateway(css.url, alice);
  ok('Alice\'s gateway is up and connected to her pod');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  const pageA = await (await browser.createBrowserContext()).newPage();
  const pageB = await (await browser.createBrowserContext()).newPage();
  const errs = [];
  pageA.on('pageerror', (e) => errs.push('A: ' + e.message));
  pageB.on('pageerror', (e) => errs.push('B: ' + e.message));

  // Everything Alice's client asks the bridge to write, and everything Bob's
  // browser POSTs into a drop box, is checked for plaintext below.
  const aliceWrites = [];
  const cdp = await pageA.createCDPSession();
  await cdp.send('Network.enable');
  cdp.on('Network.webSocketFrameSent', (e) => {
    try { const m = JSON.parse(e.response.payloadData); if (m.cmd === 'pod_fetch' && m.method === 'POST') aliceWrites.push(m); } catch { /**/ }
  });
  const bobDrops = [];
  pageB.on('request', (r) => { if (r.method() === 'POST' && /\/proxion\/dm-inbox\/$/.test(r.url())) bobDrops.push(r.postData() || ''); });

  step = 'alice-open';
  await pageA.evaluateOnNewDocument(() => {
    try { localStorage.setItem('proxion_display_name', 'Alice'); localStorage.setItem('proxion_pod_setup_skipped', '1'); } catch { /**/ }
  });
  await pageA.goto(A.url, { waitUntil: 'load', timeout: 30000 });
  await pageA.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  if (await waitFor(pageA, () => window.proxionCrossMode && window.proxionCrossMode.isReady(), undefined, 60000)) ok('Alice\'s pod bridge is ready');
  else throw new Error('pod bridge never ready: ' + JSON.stringify(await pageA.evaluate(() => window.proxionCrossMode?.state())) + '\n' + gw._log().slice(-1500));
  const signer = await (await fetch(alice.storageRoot + 'proxion/identity/signer.json')).json().catch(() => null);
  if (signer && signer.signer && signer.account_did === null) ok('Alice\'s signer.json is public and names her device key, no account did');
  else fail('signer.json: ' + JSON.stringify(signer));

  step = 'bob-signin';
  await signIn(pageB, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies, name: 'Bob' });
  await sleep(2500);
  ok('Bob signed in to the browser build');

  step = 'bob-adds-alice';
  await pageB.evaluate(() => document.getElementById('add-peer-btn').click());
  await pageB.waitForSelector('#add-peer-input', { visible: true, timeout: 10000 });
  await pageB.type('#add-peer-input', alice.webId);
  await pageB.click('#add-peer-submit-btn');
  await pageB.waitForFunction((w) => !!document.getElementById('nav-' + w), { timeout: 15000 }, alice.webId);
  const s1 = await send(pageB, 'xmode-hello-1');
  if (s1 === true) ok('Bob sent Alice an encrypted DM (drop confirmed)');
  else fail('Bob\'s first DM: ' + s1);

  step = 'request';
  const reqShown = await waitFor(pageA, (w) => !!document.querySelector(`[data-mr-webid="${CSS.escape(w)}"]`), bob.webId, 40000);
  if (reqShown) ok('Alice sees a message request from Bob\'s WebID');
  else fail('no message request on Alice\'s side');
  if (!(await feedText(pageA)).includes('xmode-hello-1')) ok('the request is not opened before Alice accepts');
  else fail('the message was shown before accepting');
  await pageA.evaluate((w) => document.querySelector(`[data-mr-webid="${CSS.escape(w)}"] [data-mr-action="accept"]`).click(), bob.webId);
  await pageA.waitForFunction((w) => !!document.getElementById('nav-' + w), { timeout: 20000 }, bob.webId).catch(() => {});
  await openThread(pageA, bob.webId);
  if (await waitFor(pageA, () => (document.getElementById('message-feed')?.innerText || '').includes('xmode-hello-1'), undefined, 30000)) ok('Alice accepted and reads Bob\'s plaintext');
  else fail('after accept Alice\'s feed: ' + JSON.stringify((await feedText(pageA)).slice(0, 300)));
  const unverifiedBadge = await pageA.evaluate(() => {
    const el = Array.from(document.querySelectorAll('#message-feed [id^="msg-"]')).find((e) => e.innerText.includes('xmode-hello-1'));
    return !!el && /unverified/i.test(el.innerText);
  });
  if (!unverifiedBadge) ok('the message is shown as verified (no unverified marker)');
  else fail('Bob\'s message is marked unverified');

  step = 'gating';
  const shown = await pageA.evaluate(() => ['attach-btn', 'voice-record-btn', 'schedule-btn']
    .filter((id) => { const el = document.getElementById(id); return el && getComputedStyle(el).display !== 'none'; }));
  if (!shown.length) ok('files, voice messages and scheduling are not offered in the WebID thread');
  else fail('still offered: ' + shown.join(', '));

  step = 'reply';
  const s2 = await send(pageA, 'xmode-reply-1');
  if (s2 === true) ok('Alice replied through her gateway\'s pod bridge');
  else fail('Alice\'s reply: ' + s2);
  await openThread(pageB, alice.webId);
  let gotReply = false;
  for (let i = 0; i < 20 && !gotReply; i++) {
    await pageB.evaluate(() => window.proxionWebDm && window.proxionWebDm.drainOnce()).catch(() => {});
    gotReply = (await feedText(pageB)).includes('xmode-reply-1');
    if (!gotReply) await sleep(1200);
  }
  if (gotReply) ok('Bob reads Alice\'s reply');
  else fail('Bob never got the reply: ' + JSON.stringify((await feedText(pageB)).slice(0, 300)));

  step = 'ciphertext';
  const leaks = [...bobDrops, ...aliceWrites.map((m) => m.body || '')].filter((b) => /xmode-/.test(b));
  const allE2e = [...bobDrops, ...aliceWrites.filter((m) => /dm-inbox\/$/.test(m.url)).map((m) => m.body)]
    .every((b) => { try { const e = JSON.parse(b); return e.e2e === true && e.signer && e.sig; } catch { return false; } });
  if (bobDrops.length && aliceWrites.length && !leaks.length && allE2e) ok(`every drop was signed ciphertext (${bobDrops.length + aliceWrites.length} checked)`);
  else fail(`plaintext or unsigned drops: leaks=${leaks.length} allE2e=${allE2e} bob=${bobDrops.length} alice=${aliceWrites.length}`);
  const inboxEmpty = async () => {
    const r = await aliceSession.fetch(alice.storageRoot + 'proxion/dm-inbox/', { headers: { Accept: 'text/turtle' } });
    const ttl = await r.text();
    return r.ok && !/ldp:contains|#contains>/.test(ttl);
  };
  let drained = false;
  for (let i = 0; i < 10 && !drained; i++) { drained = await inboxEmpty(); if (!drained) await sleep(1000); }
  if (drained) ok('Alice\'s DM inbox is empty after the drain');
  else fail('Alice\'s DM inbox still holds drops');

  step = 'presence';
  const presA = await waitFor(pageA, () => !!document.querySelector('#message-feed .avatar-presence.online'), undefined, 40000);
  if (presA) ok('Alice sees Bob online');
  else fail('Alice never saw Bob\'s presence');
  const presB = await waitFor(pageB, () => !!document.querySelector('#message-feed .avatar-presence.online'), undefined, 40000);
  if (presB) ok('Bob sees Alice online');
  else fail('Bob never saw Alice\'s presence');

  step = 'forged';
  const forged = await fetch(alice.storageRoot + 'proxion/dm-inbox/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ v: 1, from_webid: bob.webId, from_display_name: 'Bob', message_id: 'forged-1', content: 'xmode-forged-1', e2e: false, timestamp: new Date().toISOString() }),
  });
  if (!forged.ok) fail('could not drop the forged envelope: ' + forged.status);
  const notice = await waitFor(pageA, () => /not opened/i.test(document.getElementById('message-feed')?.innerText || ''), undefined, 30000);
  const forgedShown = (await feedText(pageA)).includes('xmode-forged-1');
  if (notice && !forgedShown) ok('a forged unsigned drop shows as unverified and is not opened');
  else fail(`forged drop: notice=${notice} shown=${forgedShown}`);

  step = 'reload';
  await pageA.reload({ waitUntil: 'load', timeout: 30000 });
  await pageA.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  const kept = await waitFor(pageA, (w) => !!document.getElementById('nav-' + w), bob.webId, 30000);
  if (kept) {
    await openThread(pageA, bob.webId);
    const hist = await waitFor(pageA, () => { const t = document.getElementById('message-feed')?.innerText || ''; return t.includes('xmode-hello-1') && t.includes('xmode-reply-1'); }, undefined, 15000);
    if (hist) ok('Alice keeps the WebID thread and its history after a reload');
    else fail('history after reload: ' + JSON.stringify((await feedText(pageA)).slice(0, 300)));
  } else fail('Alice lost the WebID thread on reload');
  await waitFor(pageA, () => window.proxionCrossMode && window.proxionCrossMode.isReady(), undefined, 30000);

  step = 'block';
  await pageA.evaluate((w) => {
    const li = document.getElementById('nav-' + w);
    li.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 50, clientY: 50 }));
  }, bob.webId);
  await pageA.waitForSelector('#sctx-block:not([hidden])', { timeout: 5000 });
  await pageA.click('#sctx-block');
  await sleep(800);
  await send(pageB, 'xmode-blocked-1');
  await sleep(8000);
  const blockedShown = (await feedText(pageA)).includes('xmode-blocked-1');
  let blockedGone = false;
  for (let i = 0; i < 15 && !blockedGone; i++) { blockedGone = await inboxEmpty(); if (!blockedGone) await sleep(1000); }
  if (!blockedShown && blockedGone) ok('after Alice blocks Bob his DM is deleted unread');
  else fail(`blocked DM: shown=${blockedShown} deleted=${blockedGone}`);

  if (errs.length) fail('page errors:\n    ' + errs.slice(0, 6).join('\n    '));
  else ok('no uncaught page errors on either side');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
