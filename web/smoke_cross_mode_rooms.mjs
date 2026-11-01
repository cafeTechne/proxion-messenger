// Cross-mode room smoke: Proxion desktop (a gateway with a pod) and the Proxion
// browser build sharing pod rooms, in both directions.
//
// A throwaway CSS holds two accounts. Alice runs the desktop client: a gateway
// connected to her pod (only inside this smoke, pointed at the throwaway CSS).
// Bob runs the browser build, signed in to his pod. Checks:
//   - Bob hosts a room; Alice joins with his invite link, Bob approves
//   - messages both ways, each shown as verified (Long Chat proofs)
//   - Bob removes Alice: her next append is refused (403) and her app shows it
//   - Alice hosts a room in her pod; Bob joins with her invite, which opens the
//     browser build; Alice approves on desktop
//   - messages both ways again, then Alice removes Bob and his app shows it
//
//   node web/smoke_cross_mode_rooms.mjs
// Needs Chrome (or PROXION_CHROME), python, openssl, and npx (for CSS). Self-skips
// if the cert or CSS cannot be produced.

import { existsSync, mkdtempSync } from 'fs';
import { spawn } from 'child_process';
import { tmpdir } from 'os';
import { resolve, join } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
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
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

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
  const dataDir = mkdtempSync(join(tmpdir(), 'proxion-xrooms-'));
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
  for (let i = 0; i < 120; i++) {
    if (p.exitCode !== null) throw new Error('gateway exited:\n' + log.slice(-800));
    if (await fetch(url).then((r) => r.ok).catch(() => false)) return { url };
    await sleep(500);
  }
  throw new Error('gateway HTTP never came up:\n' + log.slice(-800));
}

const feedText = (page) => page.evaluate(() => document.getElementById('message-feed')?.innerText || '');
const waitFor = (page, fn, arg, timeout = 30000) => page.waitForFunction(fn, { timeout, polling: 500 }, arg)
  .then(() => true).catch(() => false);
const roomIdOf = (invite) => decodeURIComponent(new URL(invite).searchParams.get('join').split('~')[0]);

async function openRoom(page, roomId) {
  await page.waitForFunction((id) => !!document.getElementById('nav-' + id), { timeout: 30000 }, roomId);
  await page.evaluate((id) => document.getElementById('nav-' + id)?.querySelector('.room-item-body')?.click(), roomId);
  await sleep(800);
}

async function send(page, text) {
  await page.focus('#message-input');
  await page.keyboard.type(text);
  await page.keyboard.press('Enter');
  return page.waitForFunction((t) => Array.from(document.querySelectorAll('#message-feed [id^="msg-"]'))
    .some((el) => el.innerText.includes(t) && !el.classList.contains('msg-pending')), { timeout: 25000 }, text)
    .then(() => true).catch(() => false);
}

// Wait until `text` shows in the open room, nudging the room's watcher.
async function seen(page, roomId, text, refresh) {
  for (let i = 0; i < 25; i++) {
    if ((await feedText(page)).includes(text)) return true;
    await page.evaluate(refresh, roomId).catch(() => {});
    await sleep(1200);
  }
  return false;
}
const refreshWeb = (id) => window.proxionWebRooms && window.proxionWebRooms.refresh(id);
const refreshDesk = (id) => window.proxionCrossRooms && window.proxionCrossRooms.refresh(id);
const noUnverified = (page) => page.evaluate(() => !document.querySelector('#message-feed .msg-trust-icon.text-warning'));

async function createRoom(page, name, { pod = false } = {}) {
  await page.evaluate(() => document.getElementById('create-room-btn').click());
  await page.waitForSelector('#room-name-input', { visible: true, timeout: 10000 });
  await page.type('#room-name-input', name);
  if (pod) {
    const offered = await page.evaluate(() => !document.getElementById('room-pod-row').hidden);
    if (!offered) throw new Error('the pod room option is not offered');
    await page.click('#room-pod-toggle');
  }
  await page.click('#room-create-submit');
  await page.waitForFunction(() => (document.getElementById('room-invite-url')?.textContent || '').includes('?join='), { timeout: 30000 });
  const invite = await page.$eval('#room-invite-url', (el) => el.textContent.trim());
  await page.evaluate(() => document.getElementById('room-create-done-btn')?.click());
  return invite;
}

async function requestJoin(page, invite) {
  await page.evaluate(() => document.getElementById('join-room-btn').click());
  await page.waitForSelector('#join-room-input', { visible: true, timeout: 10000 });
  await page.type('#join-room-input', invite);
  await page.click('#join-room-submit-btn');
}

// Drain a join inbox until `check` holds (the drop travels by polling).
async function drainUntil(page, drain, check, arg, tries = 20) {
  for (let i = 0; i < tries; i++) {
    await page.evaluate(drain).catch(() => {});
    if (await page.evaluate(check, arg).catch(() => false)) return true;
    await sleep(1500);
  }
  return false;
}
const drainWeb = () => window.proxionWebJoin && window.proxionWebJoin.drainOnce();
const drainDesk = () => window.proxionCrossRooms && window.proxionCrossRooms.drainJoins();
const isPrompted = () => { const m = document.getElementById('confirm-modal'); return !!m && getComputedStyle(m).display !== 'none'; };
const hasRoom = (id) => !!document.getElementById('nav-' + id);

// The owner removes a member from the open room's members panel.
async function removeMember(page, roomId, webid) {
  await page.evaluate((id) => document.querySelector(`[data-sidebar-action="members"][data-room-id="${CSS.escape(id)}"]`)?.click(), roomId);
  const row = await waitFor(page, (w) => !!document.querySelector(`#members-list .member-item[data-webid="${CSS.escape(w)}"]`), webid, 20000);
  if (!row) throw new Error('member row never listed');
  await page.evaluate((w) => {
    const item = document.querySelector(`#members-list .member-item[data-webid="${CSS.escape(w)}"]`);
    item.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 60, clientY: 60 }));
  }, webid);
  const only = await page.evaluate(() => Array.from(document.querySelectorAll('#member-context-menu button'))
    .filter((b) => !b.hidden).map((b) => b.dataset.roleAction));
  if (JSON.stringify(only) !== '["kick"]') throw new Error('pod room menu offers: ' + JSON.stringify(only));
  await page.click('#member-context-menu [data-role-action="kick"]');
  await page.waitForFunction(isPrompted, { timeout: 5000 });
  await page.click('#confirm-ok');
}

const removedShown = (page, roomId) => waitFor(page, (id) => !document.getElementById('nav-' + id)
  && /removed/i.test(document.getElementById('message-feed')?.innerText || ''), roomId, 30000);

try {
  step = 'serve';
  served = await buildAndServeHttps(HERE, join(HERE, 'scripts', 'build-web.mjs'));
  if (!served) { console.log('  \u2013 skipped: could not build/serve over https (openssl?).'); await cleanup(); process.exit(0); }

  step = 'css';
  css = await startCss({ allowSelfSignedClientId: true }).catch(() => null);
  if (!css) { console.log('  \u2013 skipped: no CSS pod available (offline / npx blocked).'); await cleanup(); process.exit(0); }
  const stamp = Date.now();
  const alice = await provisionAccount(css.url, { email: `alice-${stamp}@test.example`, password: 'pw-12345678', label: 'alice' });
  const bob = await provisionAccount(css.url, { email: `bob-${stamp}@test.example`, password: 'pw-12345678', label: 'bob' });
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

  // Alice's bridge traffic: which PATCHes into Bob's pod came back 403.
  const patchReqs = new Map(), patch403 = [];
  const cdp = await pageA.createCDPSession();
  await cdp.send('Network.enable');
  cdp.on('Network.webSocketFrameSent', (e) => {
    try { const m = JSON.parse(e.response.payloadData); if (m.cmd === 'pod_fetch' && m.method === 'PATCH') patchReqs.set(m.req_id, m.url); } catch { /**/ }
  });
  cdp.on('Network.webSocketFrameReceived', (e) => {
    try {
      const m = JSON.parse(e.response.payloadData);
      if (m.type === 'pod_fetch_result' && patchReqs.has(m.req_id) && m.status === 403 && !m.error) patch403.push(patchReqs.get(m.req_id));
    } catch { /**/ }
  });

  step = 'alice-open';
  await pageA.evaluateOnNewDocument((app) => {
    try {
      localStorage.setItem('proxion_display_name', 'Alice');
      localStorage.setItem('proxion_pod_setup_skipped', '1');
      // Her invites open this smoke's copy of the browser build.
      localStorage.setItem('proxion_web_app_url', app);
    } catch { /**/ }
  }, served.origin);
  await pageA.goto(A.url, { waitUntil: 'load', timeout: 30000 });
  await pageA.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  if (await waitFor(pageA, () => window.proxionCrossMode && window.proxionCrossMode.state().started, undefined, 60000)) ok('Alice\'s pod bridge is ready');
  else throw new Error('pod bridge never ready: ' + JSON.stringify(await pageA.evaluate(() => window.proxionCrossMode?.state())) + '\n' + gw._log().slice(-1500));

  step = 'bob-signin';
  await signIn(pageB, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies, name: 'Bob' });
  await sleep(2500);
  ok('Bob signed in to the browser build');

  // ── Bob hosts, Alice joins ────────────────────────────────────────────────
  step = 'web-hosts';
  const inviteB = await createRoom(pageB, 'xr-web-' + stamp.toString(36));
  const roomB = roomIdOf(inviteB);
  ok('Bob created a room in his pod');
  await requestJoin(pageA, inviteB);
  if (await waitFor(pageA, () => document.getElementById('join-pending-banner')?.classList.contains('visible'), undefined, 20000)) ok('Alice\'s desktop sent a join request from the browser-build link');
  else fail('no pending join on Alice\'s side');
  if (await drainUntil(pageB, drainWeb, isPrompted)) { await pageB.click('#confirm-ok'); ok('Bob was asked and approved'); }
  else fail('Bob was never prompted');
  if (await drainUntil(pageA, drainDesk, hasRoom, roomB)) ok('the room appears in Alice\'s desktop sidebar');
  else throw new Error('the room never appeared for Alice:\n' + gw._log().slice(-1500));

  step = 'web-room-messages';
  await openRoom(pageA, roomB);
  await openRoom(pageB, roomB);
  if (await send(pageA, 'xr-a-1')) ok('Alice appended to the Long Chat in Bob\'s pod');
  else fail('Alice\'s message did not confirm');
  if (await seen(pageB, roomB, 'xr-a-1', refreshWeb) && await noUnverified(pageB)) ok('Bob reads it, proof verified against Alice\'s signer');
  else fail('Bob: ' + JSON.stringify((await feedText(pageB)).slice(0, 300)));
  {
    // The message in Bob's pod carries a proof by the key Alice's signer.json names.
    const bobSession = await makeAuthSession(bob);
    const signerA = await (await fetch(alice.storageRoot + 'proxion/identity/signer.json')).json().catch(() => ({}));
    const day = new Date().toISOString().slice(0, 10).replace(/-/g, '/');
    const ttl = await (await bobSession.fetch(`${bob.storageRoot}proxion/rooms/${roomB}/${day}/chat.ttl`, { headers: { Accept: 'text/turtle' } })).text();
    if (ttl.includes('xr-a-1') && signerA.signer && ttl.includes(signerA.signer + '|') && ttl.includes(new URL(alice.webId).pathname + '#me')) ok("Alice's message in Bob's pod is signed by her device key, maker = her WebID");
    else fail("no proof by Alice's signer in Bob's day file: " + ttl.slice(0, 600));
  }
  if (await send(pageB, 'xr-b-1')) ok('Bob replied');
  else fail('Bob\'s message did not confirm');
  if (await seen(pageA, roomB, 'xr-b-1', refreshDesk) && await noUnverified(pageA)) ok('Alice reads Bob\'s reply, proof verified');
  else fail('Alice: ' + JSON.stringify((await feedText(pageA)).slice(0, 300)));

  step = 'web-removes';
  await removeMember(pageB, roomB, alice.webId);
  ok('Bob removed Alice from the room');
  await sleep(1500);
  await openRoom(pageA, roomB).catch(() => {});
  await pageA.focus('#message-input');
  await pageA.keyboard.type('xr-a-after-removal');
  await pageA.keyboard.press('Enter');
  if (await removedShown(pageA, roomB)) ok('Alice\'s app shows she was removed');
  else fail('Alice\'s UI: ' + JSON.stringify((await feedText(pageA)).slice(0, 300)));
  if (patch403.some((u) => u.startsWith(bob.storageRoot))) ok('her append to Bob\'s pod was refused with 403');
  else fail('no 403 on Alice\'s append: ' + JSON.stringify(patch403));

  // ── Alice hosts, Bob joins ────────────────────────────────────────────────
  step = 'desktop-hosts';
  const inviteA = await createRoom(pageA, 'xr-desk-' + stamp.toString(36), { pod: true });
  const roomA = roomIdOf(inviteA);
  if (inviteA.startsWith(served.origin) && /[?&]mode=web\b/.test(inviteA)) ok('Alice\'s invite opens the browser build');
  else fail('invite: ' + inviteA);
  const desc = await fetch(alice.storageRoot + `rooms/${roomA}/room.json`).then((r) => r.status);
  if (desc === 401 || desc === 403) ok('the room descriptor in Alice\'s pod is owner-only');
  else fail('descriptor status for a stranger: ' + desc);
  await requestJoin(pageB, inviteA);
  if (await drainUntil(pageA, drainDesk, isPrompted)) { await pageA.click('#confirm-ok'); ok('Alice was asked on desktop and approved'); }
  else fail('Alice was never prompted');
  if (await drainUntil(pageB, drainWeb, hasRoom, roomA)) ok('the room appears in Bob\'s sidebar');
  else throw new Error('the room never appeared for Bob');

  step = 'desktop-room-messages';
  await openRoom(pageA, roomA);
  await openRoom(pageB, roomA);
  if (await send(pageB, 'xr-b-2')) ok('Bob appended to the Long Chat in Alice\'s pod');
  else fail('Bob\'s message did not confirm');
  if (await seen(pageA, roomA, 'xr-b-2', refreshDesk) && await noUnverified(pageA)) ok('Alice reads it on desktop, proof verified');
  else fail('Alice: ' + JSON.stringify((await feedText(pageA)).slice(0, 300)));
  if (await send(pageA, 'xr-a-3')) ok('Alice replied from desktop');
  else fail('Alice\'s message did not confirm');
  if (await seen(pageB, roomA, 'xr-a-3', refreshWeb) && await noUnverified(pageB)) ok('Bob reads it, proof verified');
  else fail('Bob: ' + JSON.stringify((await feedText(pageB)).slice(0, 300)));

  step = 'desktop-removes';
  await removeMember(pageA, roomA, bob.webId);
  ok('Alice removed Bob from her room');
  await sleep(1500);
  await openRoom(pageB, roomA).catch(() => {});
  await pageB.focus('#message-input');
  await pageB.keyboard.type('xr-b-after-removal');
  await pageB.keyboard.press('Enter');
  if (await removedShown(pageB, roomA)) ok('Bob\'s app shows he was removed');
  else fail('Bob\'s UI: ' + JSON.stringify((await feedText(pageB)).slice(0, 300)));

  step = 'reload';
  await pageA.reload({ waitUntil: 'load', timeout: 30000 });
  await pageA.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  if (await waitFor(pageA, (id) => !!document.getElementById('nav-' + id), roomA, 60000)) ok('Alice still lists her pod room after a reload');
  else fail('Alice lost her pod room on reload');

  if (errs.length) fail('page errors:\n    ' + errs.slice(0, 6).join('\n    '));
  else ok('no uncaught page errors on either side');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
