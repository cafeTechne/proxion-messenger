// Cross-mode room smoke: Proxion desktop (a gateway with a pod) and the Proxion
// browser build sharing pod rooms, in both directions.
//
// A throwaway CSS holds two accounts. Alice runs the desktop client: a gateway
// connected to her pod (only inside this smoke, pointed at the throwaway CSS).
// Bob runs the browser build, signed in to his pod. Checks:
//   - Bob hosts a room; Alice joins with his invite link, Bob approves
//   - messages both ways, each shown as verified (Long Chat proofs)
//   - Bob removes idle Alice: read checks return 403 and her app shows removal
//   - Alice hosts a room in her pod; Bob joins with her invite, which opens the
//     browser build; Alice approves on desktop
//   - messages both ways again, then idle Bob sees Alice's removal without posting
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

async function messageMenu(page, text, action) {
  const id = await page.evaluate((text) => {
    const el = Array.from(document.querySelectorAll('#message-feed [id^="msg-"]')).find(el => el.querySelector('.msg-text')?.innerText === text);
    if (!el) throw Error('Missing message: ' + text);
    el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 300, clientY: 200 }));
    return el.id;
  }, text);
  await page.click('#ctx-' + action);
  return id;
}
async function editMessage(page, oldText, newText) {
  await messageMenu(page, oldText, 'edit');
  await page.waitForSelector('.edit-input');
  await page.evaluate(text => { document.querySelector('.edit-input').value = text; }, newText);
  await page.locator('.edit-confirm-btn').click();
}
async function deleteMessage(page, text) {
  const id = await messageMenu(page, text, 'delete');
  await page.waitForSelector('#confirm-ok', { visible: true });
  await page.click('#confirm-ok');
  if (!(await waitFor(page, id => !document.getElementById(id), id, 30000))) throw Error('Deletion was not confirmed');
  return id;
}
async function openPins(page) {
  await page.locator('#pin-panel-btn').click();
  await page.waitForSelector('#pin-panel', { visible: true });
}
async function expectPin(page, text, present = true) {
  if (!(await waitFor(page, ({ text, present }) => {
    const list = document.getElementById('pin-list');
    return !!list && list.innerText.includes(text) === present && !list.innerText.includes('Loading');
  }, { text, present }, 45000))) throw Error('Unexpected pin list for ' + text + ': ' + await page.evaluate(() => document.getElementById('pin-list')?.innerText));
}
async function jumpPin(page, id) {
  await page.locator(`#pin-list [data-pin-action="jump"][data-msg-id="${id}"]`).click();
  if (!(await waitFor(page, id => !!document.getElementById('msg-' + id), id, 20000))) throw Error('Could not jump to old pin ' + id);
}

async function addReaction(page, text, emoji) {
  const id = await messageMenu(page, text, 'react');
  await page.waitForSelector('#emoji-picker', { visible: true });
  await page.locator(`#emoji-picker [data-emoji="${emoji}"]`).click();
  return id;
}
async function toggleReaction(page, id, emoji) {
  await page.evaluate(({ id, emoji }) => {
    const pill = Array.from(document.getElementById(id)?.querySelectorAll('.reaction:not(.reaction-add)') || [])
      .find(el => el.innerText.startsWith(emoji + ' '));
    if (!pill) throw Error('Missing reaction pill');
    pill.click();
  }, { id, emoji });
}
async function expectReaction(page, roomId, id, emoji, count, own, refresh) {
  for (let i = 0; i < 25; i++) {
    const matches = await page.evaluate(({ id, emoji, count, own }) => {
      const message = document.getElementById(id);
      if (!message) return false;
      const pill = Array.from(message.querySelectorAll('.reaction:not(.reaction-add)')).find(el => el.innerText.startsWith(emoji + ' '));
      return count === 0 ? !pill : pill?.innerText === emoji + ' ' + count && pill.classList.contains('active') === own;
    }, { id, emoji, count, own });
    if (matches) return;
    await page.evaluate(refresh, roomId).catch(() => {});
    await sleep(600);
  }
  throw Error(`Reaction ${emoji} ${count} own=${own} did not appear for ${id}`);
}

async function changedOnPeer(page, roomId, oldText, newText, refresh) {
  if (!(await seen(page, roomId, newText, refresh))) throw Error('Peer did not receive edited text');
  if ((await feedText(page)).includes(oldText)) throw Error('Peer retained stale text');
}

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
// A drain can wait for the owner's confirmation. Start it without awaiting so
// the harness can observe that prompt and click the approval button.
const drainWeb = () => { window.proxionWebJoin?.drainOnce().catch(() => {}); };
const drainDesk = () => { window.proxionCrossRooms?.drainJoins().catch(() => {}); };
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
  && /removed/i.test(document.getElementById('message-feed')?.innerText || ''), roomId, 45000);

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
  ok('CSS up, Alice (desktop) and Bob (browser build) provisioned');

  step = 'gateway';
  const A = await startGateway(css.url, alice);
  ok('Alice\'s gateway is up and connected to her pod');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  const pageA = await (await browser.createBrowserContext()).newPage();
  const pageB = await (await browser.createBrowserContext()).newPage();
  const errs = [];
  const deleteOps = [];
  for (const page of [pageA, pageB]) {
    page.on('console', message => {
      if (message.text().includes('Room deletion failed:')) deleteOps.push(message.text());
      if (message.text().includes('Message change failed:')) console.log('    ' + message.text());
    });
    page.on('response', response => {
      if (step.endsWith('-deletes') && response.request().method() !== 'OPTIONS'
          && /\/(room\.json|\.acl|members\.jsonld|publicTypeIndex\.ttl)$/.test(new URL(response.url()).pathname)) {
        deleteOps.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
      }
    });
  }
  pageA.on('pageerror', (e) => errs.push('A: ' + e.message));
  pageB.on('pageerror', (e) => errs.push('B: ' + e.message));

  // Alice's bridge traffic: idle membership HEAD checks and room appends.
  const patchReqs = new Map(), memberReqs = new Map(), deniedMemberReads = [];
  const deniedBrowserReads = [];
  pageB.on('response', (r) => {
    if (r.request().method() === 'HEAD' && r.status() === 403 && r.url().endsWith('/members.jsonld')) deniedBrowserReads.push(r.url());
  });
  const cdp = await pageA.createCDPSession();
  await cdp.send('Network.enable');
  cdp.on('Network.webSocketFrameSent', (e) => {
    try {
      const m = JSON.parse(e.response.payloadData);
      if (m.cmd === 'pod_fetch' && m.method === 'PATCH') patchReqs.set(m.req_id, m.url);
      if (m.cmd === 'pod_fetch' && m.method === 'HEAD' && m.url.endsWith('/members.jsonld')) memberReqs.set(m.req_id, m.url);
    } catch { /**/ }
  });
  cdp.on('Network.webSocketFrameReceived', (e) => {
    try {
      const m = JSON.parse(e.response.payloadData);
      if (m.type === 'pod_fetch_result' && memberReqs.has(m.req_id) && m.status === 403 && !m.error) deniedMemberReads.push(memberReqs.get(m.req_id));
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
  // Keep approval's access write unavailable through an owner reload. The
  // retained request must have a persistent, actionable status and recover.
  let blockApproval = true, blockMessageChanges = false, blockedChanges = 0, blockPinWrites = false, blockedPins = 0;
  await pageB.setRequestInterception(true);
  pageB.on('request', request => {
    if (blockPinWrites && request.method() === 'PUT' && request.url().endsWith('/pins.json')) {
      blockedPins++; request.abort('failed').catch(() => {});
    } else if (blockMessageChanges && request.method() === 'PATCH' && request.url().endsWith('/chat.ttl')) {
      blockedChanges++;
      request.abort('failed').catch(() => {});
    } else if (blockApproval && request.method() === 'PUT'
        && request.url() === `${bob.storageRoot}proxion/rooms/${roomB}/.acl`) {
      request.abort('failed').catch(() => {});
    } else request.continue().catch(() => {});
  });
  await requestJoin(pageA, inviteB);
  if (await waitFor(pageA, () => document.getElementById('join-pending-banner')?.classList.contains('visible'), undefined, 20000)) ok('Alice\'s desktop sent a join request from the browser-build link');
  else fail('no pending join on Alice\'s side');
  if (await drainUntil(pageB, drainWeb, isPrompted)) { await pageB.click('#confirm-ok'); ok('Bob was asked and approved'); }
  else fail('Bob was never prompted');
  if (!(await waitFor(pageB, () => document.getElementById('room-approval-status')?.classList.contains('visible'), undefined, 20000))) throw new Error('owner approval failure has no persistent retry status');
  await pageB.reload({ waitUntil: 'load', timeout: 30000 });
  await pageB.evaluate(() => { const modal = document.getElementById('onboarding-modal'); if (modal) modal.style.display = 'none'; });
  if (!(await waitFor(pageB, () => document.getElementById('room-approval-status')?.classList.contains('visible'), undefined, 30000))) throw new Error('owner retry status was lost on reload');
  blockApproval = false;
  await pageB.click('#room-approval-status button');
  if (await drainUntil(pageA, drainDesk, hasRoom, roomB)) ok('the room appears in Alice\'s desktop sidebar');
  else throw new Error('the room never appeared for Alice:\n' + gw._log().slice(-1500));
  if (!(await waitFor(pageB, () => !document.getElementById('room-approval-status')?.classList.contains('visible'), undefined, 20000))) throw new Error('owner retry status did not clear after success');
  ok('failed owner approval survives reload and Retry completes it');

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

  step = 'web-room-pins';
  await messageMenu(pageB, 'xr-a-1', 'pin');
  await openPins(pageB); await expectPin(pageB, 'xr-a-1');
  await openPins(pageA); await expectPin(pageA, 'xr-a-1');
  if (await pageA.evaluate(() => !!document.querySelector('#pin-list [data-pin-action="unpin"]'))) throw Error('Member can unpin');
  const refused = await pageA.evaluate(({ id, timestamp }) => window.proxionCrossRooms.setPin(id, 'arbitrary', timestamp, true), { id: roomB, timestamp: new Date().toISOString() });
  if (refused.ok || refused.reason !== 'forbidden') throw Error('Programmatic member pin was allowed');
  blockPinWrites = true;
  await pageB.locator('#pin-list [data-pin-action="unpin"]').click();
  for (let i = 0; i < 50 && !blockedPins; i++) await sleep(100);
  if (!blockedPins) throw Error('Unpin did not attempt a conditional write');
  await sleep(600); await expectPin(pageB, 'xr-a-1');
  blockPinWrites = false;
  await pageB.locator('#pin-list [data-pin-action="unpin"]').click();
  await expectPin(pageB, 'xr-a-1', false); await expectPin(pageA, 'xr-a-1', false);
  await pageB.click('#pin-panel-close'); await pageA.click('#pin-panel-close');
  await messageMenu(pageB, 'xr-a-1', 'pin');
  const oldPinDate = new Date(Date.now() - 14 * 86400000).toISOString();
  const oldWebResult = await pageB.evaluate(async ({ container, owner, timestamp }) => {
    const api = await import('./pod.js');
    const written = await api.podWriteChatMessageAt(container, 'old-web-pin', { timestamp, content: 'xr-old-web-pin', from_webid: owner });
    return written && await api.podSetPinAt(container, 'old-web-pin', timestamp, true, owner);
  }, { container: `${bob.storageRoot}proxion/rooms/${roomB}/`, owner: bob.webId, timestamp: oldPinDate });
  if (!oldWebResult?.ok) throw Error('Could not persist old browser pin: ' + JSON.stringify(oldWebResult));
  await openPins(pageA); await expectPin(pageA, 'xr-old-web-pin');
  await jumpPin(pageA, 'old-web-pin'); await pageA.click('#pin-panel-close');
  ok('browser owner pins; member reads/jumps old history; rejected unpin remains retryable');

  step = 'web-room-reactions';
  const reactionTarget = await addReaction(pageA, 'xr-a-1', '👍');
  await expectReaction(pageA, roomB, reactionTarget, '👍', 1, true, refreshDesk);
  await expectReaction(pageB, roomB, reactionTarget, '👍', 1, false, refreshWeb);
  await addReaction(pageB, 'xr-a-1', '👍');
  await expectReaction(pageB, roomB, reactionTarget, '👍', 2, true, refreshWeb);
  await expectReaction(pageA, roomB, reactionTarget, '👍', 2, true, refreshDesk);
  await toggleReaction(pageB, reactionTarget, '👍');
  await expectReaction(pageB, roomB, reactionTarget, '👍', 1, false, refreshWeb);
  await expectReaction(pageA, roomB, reactionTarget, '👍', 1, true, refreshDesk);
  await toggleReaction(pageA, reactionTarget, '👍');
  await expectReaction(pageA, roomB, reactionTarget, '👍', 0, false, refreshDesk);
  await expectReaction(pageB, roomB, reactionTarget, '👍', 0, false, refreshWeb);
  await addReaction(pageA, 'xr-a-1', '👍');
  await expectReaction(pageB, roomB, reactionTarget, '👍', 1, false, refreshWeb);
  if (await pageB.evaluate(id => !!document.getElementById(id)?.querySelector('.edited-tag'), reactionTarget)) throw Error('Reaction incorrectly marked message edited');
  ok('pod reactions merge two users and support removal/re-add without changing message text');

  step = 'web-room-changes';
  await editMessage(pageA, 'xr-a-1', 'xr-a-edited');
  await changedOnPeer(pageB, roomB, 'xr-a-1', 'xr-a-edited', refreshWeb);
  await pageB.reload({ waitUntil: 'load', timeout: 30000 });
  await openRoom(pageB, roomB);
  if (!(await seen(pageB, roomB, 'xr-a-edited', refreshWeb))) throw Error('Edited message was lost on reload');
  await expectReaction(pageB, roomB, reactionTarget, '👍', 1, false, refreshWeb);
  await openPins(pageB); await expectPin(pageB, 'xr-a-edited'); await expectPin(pageB, 'xr-old-web-pin');
  const removedA = await deleteMessage(pageA, 'xr-a-edited');
  if (!(await waitFor(pageB, id => !document.getElementById(id), removedA, 30000))) throw Error('Peer did not receive author deletion');
  await expectPin(pageB, 'xr-a-edited', false);
  await pageB.click('#pin-panel-close');
  ok('desktop participant edits and deletes in browser-owned room; pins reflect edits and withdrawal after reload');

  step = 'web-removes';
  await removeMember(pageB, roomB, alice.webId);
  ok('Bob removed Alice from the room');
  const appendCount = patchReqs.size;
  if (await removedShown(pageA, roomB)) ok('idle Alice sees removal without sending another message');
  else fail('Alice\'s UI: ' + JSON.stringify((await feedText(pageA)).slice(0, 300)));
  if (deniedMemberReads.includes(`${bob.storageRoot}proxion/rooms/${roomB}/members.jsonld`)) ok('Alice confirmed lost read access with her own pod session still usable');
  else fail('no 403 from Alice\'s idle member read: ' + JSON.stringify(deniedMemberReads));
  if (patchReqs.size !== appendCount) fail('Alice posted to discover removal');

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

  step = 'desktop-room-pins';
  await messageMenu(pageA, 'xr-b-2', 'pin');
  await openPins(pageA); await expectPin(pageA, 'xr-b-2'); await pageA.click('#pin-panel-close');
  await openPins(pageB); await expectPin(pageB, 'xr-b-2');
  if (await pageB.evaluate(() => !!document.querySelector('#pin-list [data-pin-action="unpin"]'))) throw Error('Browser member can unpin');
  await pageB.click('#pin-panel-close');
  const oldDeskResult = await pageA.evaluate(async ({ container, owner, timestamp }) => {
    const io = window.proxionCrossRooms.io;
    const written = await io.podWriteChatMessageAt(container, 'old-desk-pin', { timestamp, content: 'xr-old-desk-pin', from_webid: owner });
    return written && await io.podSetPinAt(container, 'old-desk-pin', timestamp, true, owner);
  }, { container: `${alice.storageRoot}proxion/rooms/${roomA}/`, owner: alice.webId, timestamp: oldPinDate });
  if (!oldDeskResult?.ok) throw Error('Could not persist old desktop pin: ' + JSON.stringify(oldDeskResult));
  ok('desktop owner pins in her pod; browser participant reads the signed index');

  step = 'desktop-room-reactions';
  const beforeBlockedReaction = blockedChanges;
  blockMessageChanges = true;
  const desktopReactionTarget = await addReaction(pageB, 'xr-a-3', '👍');
  for (let i = 0; i < 50 && blockedChanges === beforeBlockedReaction; i++) await sleep(100);
  if (blockedChanges === beforeBlockedReaction) throw Error('Reaction did not attempt its pod write');
  // Wait until the failed operation settles before retrying the same emoji.
  await sleep(1000);
  await expectReaction(pageB, roomA, desktopReactionTarget, '👍', 0, false, refreshWeb);
  await expectReaction(pageA, roomA, desktopReactionTarget, '👍', 0, false, refreshDesk);
  blockMessageChanges = false;
  await addReaction(pageB, 'xr-a-3', '👍');
  await expectReaction(pageB, roomA, desktopReactionTarget, '👍', 1, true, refreshWeb);
  await expectReaction(pageA, roomA, desktopReactionTarget, '👍', 1, false, refreshDesk);
  await addReaction(pageB, 'xr-a-3', '🎉');
  await addReaction(pageB, 'xr-a-3', '🔥');
  await expectReaction(pageA, roomA, desktopReactionTarget, '🎉', 1, false, refreshDesk);
  await expectReaction(pageA, roomA, desktopReactionTarget, '🔥', 1, false, refreshDesk);
  ok('browser participant retries rejected reaction and preserves rapid emoji additions in desktop room');

  step = 'desktop-room-changes';
  blockMessageChanges = true;
  await editMessage(pageB, 'xr-b-2', 'xr-b-edited');
  for (let i = 0; i < 50 && !blockedChanges; i++) await sleep(100);
  if (!blockedChanges) throw Error('Edit Save did not attempt its pod write');
  if (!(await waitFor(pageB, () => {
    const input = document.querySelector('.edit-input');
    return input && !input.disabled && input.value === 'xr-b-edited';
  }, undefined, 15000))) throw Error('Failed edit lost its draft');
  if (!(await feedText(pageA)).includes('xr-b-2')) throw Error('Failed edit changed peer message');
  blockMessageChanges = false;
  await pageB.locator('.edit-confirm-btn').click();
  if (!(await waitFor(pageB, () => !document.querySelector('.edit-input'), undefined, 20000))) {
    const state = await pageB.evaluate(() => ({ feed: document.getElementById('message-feed')?.innerText,
      draft: document.querySelector('.edit-input')?.value, disabled: document.querySelector('.edit-input')?.disabled,
      notices: Array.from(document.querySelectorAll('[role=alert], .toast')).map(el => el.innerText) }));
    await pageB.screenshot({ path: '/tmp/proxion-room-edit-retry.png' });
    throw Error('Retry edit did not confirm: ' + JSON.stringify({ ...state, errors: errs }));
  }
  await changedOnPeer(pageA, roomA, 'xr-b-2', 'xr-b-edited', refreshDesk);
  // Owner moderation uses the same signed append-only withdrawal protocol.
  const removedB = await deleteMessage(pageA, 'xr-b-edited');
  if (!(await waitFor(pageB, id => !document.getElementById(id), removedB, 30000))) throw Error('Browser did not receive owner deletion');
  await openRoom(pageB, roomB);
  if ((await feedText(pageB)).includes('xr-a-edited')) throw Error('Deleted message returned after reopening');
  await openRoom(pageB, roomA);
  ok('browser participant retries failed edits in desktop-owned room; owner withdrawal reaches browser');

  step = 'desktop-removes';
  await removeMember(pageA, roomA, bob.webId);
  ok('Alice removed Bob from her room');
  if (await removedShown(pageB, roomA)) ok('idle Bob sees removal without sending another message');
  else fail('Bob\'s UI: ' + JSON.stringify((await feedText(pageB)).slice(0, 300)));
  if (!deniedBrowserReads.includes(`${alice.storageRoot}proxion/rooms/${roomA}/members.jsonld`)) fail('no 403 from Bob\'s idle member read');

  step = 'reload';
  await pageA.reload({ waitUntil: 'load', timeout: 30000 });
  await pageA.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  if (await waitFor(pageA, (id) => !!document.getElementById('nav-' + id), roomA, 60000)) ok('Alice still lists her pod room after a reload');
  else fail('Alice lost her pod room on reload');

  await openRoom(pageA, roomA);
  for (const emoji of ['👍', '🎉', '🔥']) await expectReaction(pageA, roomA, desktopReactionTarget, emoji, 1, false, refreshDesk);
  ok('desktop history restores persisted reactions after reload');

  await openPins(pageA); await expectPin(pageA, 'xr-old-desk-pin');
  if (await pageA.evaluate(() => !!document.getElementById('msg-old-desk-pin'))) throw Error('Old pin unexpectedly in ordinary 7-day history');
  await jumpPin(pageA, 'old-desk-pin'); await pageA.click('#pin-panel-close');
  ok('desktop reload restores old pins outside the history window and Jump loads the original day');

  // Rejoin the kicked rooms, then delete them from each host. A deleted room
  // must revoke real pod reads and writes, including for an idle participant.
  for (const flow of [
    { ownerPage: pageB, peerPage: pageA, owner: bob, peer: alice, id: roomB, invite: inviteB,
      ownerDrain: drainWeb, peerDrain: drainDesk, label: 'browser' },
    { ownerPage: pageA, peerPage: pageB, owner: alice, peer: bob, id: roomA, invite: inviteA,
      ownerDrain: drainDesk, peerDrain: drainWeb, label: 'desktop' },
  ]) {
    step = flow.label + '-deletes';
    await requestJoin(flow.peerPage, flow.invite);
    if (!(await drainUntil(flow.ownerPage, flow.ownerDrain, isPrompted))) throw new Error('no rejoin approval prompt');
    await flow.ownerPage.click('#confirm-ok');
    if (!(await drainUntil(flow.peerPage, flow.peerDrain, hasRoom, flow.id))) throw new Error('rejoin not restored');
    await openRoom(flow.peerPage, flow.id);
    await openRoom(flow.ownerPage, flow.id);
    await flow.ownerPage.keyboard.press('Escape');
    await flow.ownerPage.click('#room-options-btn');
    await flow.ownerPage.waitForSelector('#delete-room-btn', { visible: true, timeout: 5000 });
    await flow.ownerPage.click('#delete-room-btn');
    await flow.ownerPage.waitForFunction(isPrompted, { timeout: 5000 });
    await flow.ownerPage.click('#confirm-ok');
    if (!(await waitFor(flow.ownerPage, id => !document.getElementById('nav-' + id), flow.id, 30000))) {
      const session = await makeAuthSession(flow.owner);
      const descriptor = await (await session.fetch(`${flow.owner.storageRoot}rooms/${flow.id}/room.json`)).text();
      const toast = await flow.ownerPage.evaluate(() => document.querySelector('#toast-container')?.innerText || document.querySelector('.toast')?.innerText || '');
      throw new Error('owner deletion did not finish: ' + toast + '\n' + descriptor + '\n' + deleteOps.slice(-35).join('\n'));
    }
    if (!(await removedShown(flow.peerPage, flow.id))) throw new Error('idle participant still lists deleted room');
    const peerSession = await makeAuthSession(flow.peer);
    const ownerSession = await makeAuthSession(flow.owner);
    const container = `${flow.owner.storageRoot}proxion/rooms/${flow.id}/`;
    const day = new Date().toISOString().slice(0, 10).replace(/-/g, '/');
    const dayUrl = `${container}${day}/chat.ttl`;
    const read = await peerSession.fetch(dayUrl);
    const write = await peerSession.fetch(dayUrl, { method: 'PATCH',
      headers: { 'Content-Type': 'application/sparql-update' },
      body: 'INSERT DATA { <urn:proxion:deletion-probe> <urn:proxion:test> "denied" . }' });
    if (read.status !== 403 || write.status !== 403) throw new Error(`deleted participant access: GET ${read.status}, PATCH ${write.status}`);
    const history = await ownerSession.fetch(dayUrl);
    if (!history.ok) throw new Error('owner lost retained history: ' + history.status);
    const closed = await (await ownerSession.fetch(`${flow.owner.storageRoot}rooms/${flow.id}/room.json`)).json();
    if (!closed.closed || !closed.deletion_complete || closed.members.length !== 1 || closed.members[0].webid !== flow.owner.webId) throw new Error('missing completed owner-only tombstone');
    ok(`${flow.label} deletion removes idle participants and denies GET/PATCH; owner retains history and closure record`);
  }

  if (errs.length) fail('page errors:\n    ' + errs.slice(0, 6).join('\n    '));
  else ok('no uncaught page errors on either side');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
