// Cross-account, multi-device DM smoke (R52 E6) — the "never green-verified"
// case from memory/multidevice-status.md.
//
// Topology: gateway A hosts account A on TWO paired devices (A-primary +
// A-device, linked via the delegation pairing flow); gateway B hosts peer B.
// B and A friend across gateways, then B sends A a DM. Assertions:
//   1. Pairing links A-device to account A (delegation cert for A's account).
//   2. The cross-gateway E2E DM from B reaches AND decrypts on A-primary.
//   3. A-device DEGRADES GRACEFULLY: it does NOT render "[could not decrypt]".
//   4. After reloading, the DM is still readable plaintext on A-primary (dmhistory).
//
// Scope note: CROSS-GATEWAY multi-device fanout is not implemented — a peer on
// another gateway can't resolve A's per-device keys, so it single-sends to the
// primary. So A-device does NOT receive a decryptable copy of a cross-gateway
// DM (and thus has no history for it). This smoke guards the SUPPORTED flow
// (pairing + cross-gateway DM to the primary + primary history) plus the graceful
// degradation on the secondary device. The full "cross-account N-device live
// decrypt + history" is the deferred cross-gateway-device-resolution round.
//
//   node web/smoke_multidevice_dm.mjs     (spawns two gateways; needs python + chrome)
//
// Exit 0 = the four assertions above held. A non-zero exit with a specific step
// tells us exactly which layer (pairing / federation / degradation / history) broke.
import { existsSync, mkdtempSync, rmSync } from 'fs';
import { spawn } from 'child_process';
import { createServer, connect } from 'net';
import { tmpdir } from 'os';
import { join, resolve } from 'path';
import puppeteer from 'puppeteer-core';

const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find(p => p && existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

const REPO = resolve(process.cwd(), '..');
const WEB = resolve(REPO, 'web');
const MSG = 'mddm-' + Math.random().toString(36).slice(2, 7);

const freePort = () => new Promise((res, rej) => {
  const s = createServer();
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
  s.on('error', rej);
});
const waitForPort = (port, ms) => new Promise((res, rej) => {
  const deadline = Date.now() + ms;
  const tryOnce = () => {
    const c = connect(port, '127.0.0.1');
    c.once('connect', () => { c.destroy(); res(); });
    c.once('error', () => { c.destroy(); Date.now() > deadline ? rej(new Error(`port ${port} not up`)) : setTimeout(tryOnce, 300); });
  };
  tryOnce();
});
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const procs = [], dirs = [];
async function startGateway(name) {
  const httpPort = await freePort(), wsPort = await freePort();
  const dataDir = mkdtempSync(join(tmpdir(), `proxion-md-${name}-`));
  dirs.push(dataDir);
  const env = {
    ...process.env,
    PROXION_DATA_DIR: dataDir, PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
    PROXION_HOST: '127.0.0.1', PROXION_PUBLIC_URL: '', PROXION_REQUIRE_AUTH: '0',
    PROXION_CSS_URL: '', PROXION_CSS_EMAIL: '', PROXION_CSS_PASSWORD: '',
    PROXION_ALLOW_PRIVATE_RELAY: '1', PROXION_WEB_DIR: WEB,
    PROXION_ALLOW_INSECURE_FEDERATION: '1',
    PROXION_LOG_LEVEL: process.env.PROXION_LOG_LEVEL || 'INFO',
  };
  const p = spawn('python', ['scripts/run_test_gateway.py'], { cwd: REPO, env });
  procs.push(p);
  let log = '';
  p._log = () => log;
  p.stdout.on('data', d => log += d); p.stderr.on('data', d => log += d);
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && !/PROXION_GATEWAY_READY/.test(log)) {
    if (p.exitCode !== null) throw new Error(`${name} gateway exited:\n${log.slice(-600)}`);
    await sleep(300);
  }
  if (!/PROXION_GATEWAY_READY/.test(log)) throw new Error(`${name} not ready:\n${log.slice(-600)}`);
  await waitForPort(httpPort, 15000);
  return { url: `http://127.0.0.1:${httpPort}/`, httpPort, wsPort };
}

const online = (page) => page.waitForFunction(
  () => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 });

async function openClient(ctx, url, label) {
  const page = await ctx.newPage();
  page._console = [];
  page.on('console', m => page._console.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', e => { page._console.push('PAGEERR: ' + e.message); console.error(`  [${label}] pageerror: ${e.message}`); });
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await online(page);
  return page;
}

const feedHas = (page, t) => page.evaluate((x) => (document.getElementById('message-feed')?.textContent || '').includes(x), t);
async function openFirstDm(page, who) {
  await page.waitForFunction(() => document.querySelector('#contacts-list li, #dm-list li'), { timeout: 20000 });
  await sleep(400);
  await page.evaluate(() => (document.querySelector('#dm-list li, #contacts-list li')).click());
  await page.waitForFunction(() => {
    const h = document.getElementById('chat-header-name'); return h && !/welcome/i.test(h.textContent || '');
  }, { timeout: 8000 });
}

let step = 'init';
let browser = null;
const fail = (m) => { console.error(`  ✗ [${step}] ${m}`); process.exitCode = 1; };
try {
  step = 'spawn-gateways';
  const [A, B] = [await startGateway('acct-a'), await startGateway('acct-b')];
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
  });

  // Separate browser contexts: A-primary and A-device are the SAME origin
  // (gateway A) so they MUST be isolated to hold distinct identities.
  step = 'open-clients';
  const ctxAP = await browser.createBrowserContext();
  const ctxAD = await browser.createBrowserContext();
  const ctxB = await browser.createBrowserContext();
  const ap = await openClient(ctxAP, A.url, 'A-primary');
  const b = await openClient(ctxB, B.url, 'B');
  const apDid = await ap.evaluate(() => localStorage.getItem('proxion_identity_did'));

  // ── Pair a second device to account A ──
  step = 'pair-start';
  await ap.evaluate(() => document.getElementById('link-device-btn').click());
  await ap.waitForFunction(() => (document.getElementById('device-link-code')?.textContent || '').length > 0,
    { timeout: 10000 }).catch(() => fail('A-primary never got a pairing code'));
  if (process.exitCode) throw new Error('stop');
  const code = await ap.evaluate(() => document.getElementById('device-link-code').textContent.trim());

  step = 'pair-submit';
  const ad = await openClient(ctxAD, A.url, 'A-device');
  const adDid = await ad.evaluate(() => localStorage.getItem('proxion_identity_did'));
  if (adDid === apDid) fail('A contexts not isolated (same clientDid)');
  await ad.evaluate(() => document.getElementById('ob-link-device').click());
  await ad.evaluate((c) => {
    document.getElementById('pair-code-input').value = c;
    document.getElementById('pair-device-submit').click();
  }, code);

  step = 'pair-approve';
  await ap.waitForFunction(
    () => getComputedStyle(document.getElementById('device-link-approve-row')).display !== 'none',
    { timeout: 10000 }).catch(() => fail('A-primary never got the pairing request'));
  if (process.exitCode) throw new Error('stop');
  const sP = await ap.evaluate(() => document.getElementById('device-link-safety').textContent.trim());
  const sN = await ad.evaluate(() => document.getElementById('pair-device-safety').textContent.trim());
  if (!sP || sP !== sN) fail(`safety code mismatch: ${sP} vs ${sN}`);
  await ap.evaluate(() => document.getElementById('device-link-approve').click());

  step = 'pair-relink';
  await ad.waitForFunction(() => !!localStorage.getItem('proxion_delegation_cert'), { timeout: 10000 })
    .catch(() => fail('A-device never stored the delegation cert'));
  if (process.exitCode) throw new Error('stop');
  const certAcct = await ad.evaluate(() => { try { return JSON.parse(localStorage.getItem('proxion_delegation_cert')).account_did; } catch { return null; } });
  if (certAcct !== apDid) fail(`A-device cert account ${certAcct} != A-primary ${apDid}`);
  await sleep(2200);           // A-device reloads ~1.2s post-approval
  await online(ad).catch(() => fail('A-device did not reconnect as the account'));
  if (process.exitCode) throw new Error('stop');

  // ── Cross-gateway friendship: A-primary adds B, B accepts ──
  step = 'read-b-address';
  await b.evaluate(() => document.getElementById('settings-btn')?.click());
  const bAddr = await b.waitForFunction(
    () => window.proxionAddress || localStorage.getItem('proxion_my_address'), { timeout: 10000 })
    .then(h => h.jsonValue()).catch(() => null);
  await b.evaluate(() => { const m = document.getElementById('settings-modal'); if (m) m.style.display = 'none'; });
  if (!bAddr || !bAddr.includes('@')) { fail(`could not read B's address (${bAddr})`); throw new Error('stop'); }

  step = 'a-add-b';
  await ap.evaluate(() => document.getElementById('add-peer-btn').click());
  await ap.waitForFunction(() => { const m = document.getElementById('add-peer-modal'); return m && getComputedStyle(m).display !== 'none'; }, { timeout: 5000 });
  await ap.type('#add-peer-input', bAddr);
  await ap.evaluate(() => document.getElementById('add-peer-submit-btn').click());

  step = 'b-accept';
  await b.waitForFunction(() => document.querySelector('#friend-request-list li [data-fr-action="accept"]'), { timeout: 20000 })
    .catch(() => fail('friend request never reached B'));
  if (process.exitCode) throw new Error('stop');
  await b.evaluate(() => document.querySelector('#friend-request-list li [data-fr-action="accept"]').click());

  // Open the DM on all three so an inbound relay is live feed content, not a badge.
  step = 'open-dms';
  await openFirstDm(b, 'B').catch(() => fail('B could not open the DM with A'));
  await openFirstDm(ap, 'A-primary').catch(() => fail('A-primary could not open the DM with B'));
  await openFirstDm(ad, 'A-device').catch(() => fail('A-device could not open the DM with B'));
  if (process.exitCode) throw new Error('stop');

  // ── B sends the DM; BOTH of A's devices must receive + decrypt it ──
  step = 'b-send-dm';
  await sleep(400);
  await b.focus('#message-input');
  await b.type('#message-input', MSG);
  await b.keyboard.press('Enter');

  step = 'a-primary-receive';
  await ap.waitForFunction((t) => document.getElementById('message-feed')?.textContent.includes(t), { timeout: 15000 }, MSG)
    .catch(async () => {
      const feed = await ap.evaluate(() => document.getElementById('message-feed')?.textContent || '');
      fail(/could not decrypt|decryption error/i.test(feed)
        ? `DM reached A-primary but failed to DECRYPT` : `DM never reached A-primary`);
    });
  if (process.exitCode) throw new Error('stop');

  step = 'a-device-graceful';  // cross-gateway 2nd device: no decryptable copy —
  // must degrade gracefully (drop it), NOT render "[could not decrypt]" garbage.
  await sleep(2500);           // give any relayed copy time to (not) render
  const adFeed = await ad.evaluate(() => document.getElementById('message-feed')?.textContent || '');
  if (/could not decrypt|decryption error/i.test(adFeed)) {
    fail('A-device rendered "[could not decrypt]" for a cross-gateway DM it structurally cannot read (should drop silently)');
    console.error(`  · A-device feed tail: "${adFeed.slice(-120)}"`);
  }
  if (process.exitCode) throw new Error('stop');

  // ── Reload A-primary; the DM must remain readable plaintext (dmhistory) ──
  step = 'reload-a-primary';
  await ap.reload({ waitUntil: 'load', timeout: 20000 });
  await ap.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await online(ap).catch(() => fail('A-primary did not reconnect after reload'));
  if (process.exitCode) throw new Error('stop');

  step = 'reopen-and-assert';
  await openFirstDm(ap, 'A-primary').catch(() => fail('A-primary could not reopen the DM after reload'));
  if (process.exitCode) throw new Error('stop');
  await sleep(1500);
  if (!(await feedHas(ap, MSG))) {
    const feed = await ap.evaluate(() => document.getElementById('message-feed')?.textContent || '');
    fail(/could not decrypt|decryption error/i.test(feed)
      ? `A-primary: DM shows "[could not decrypt]" after reload — plaintext not persisted`
      : `A-primary: DM "${MSG}" missing from history after reload`);
  }

  step = 'done';
  if (!process.exitCode) {
    console.log(`  ✓ multi-device DM OK — A-device paired to account A; B's cross-gateway E2E DM`);
    console.log(`    decrypted on A-primary + survived reload; A-device degraded gracefully`);
    console.log(`    (no "[could not decrypt]") for the cross-gateway copy it can't read.`);
  }
} catch (e) {
  if (e.message !== 'stop') console.error(`  ✗ [${step}] threw: ${e.message}`);
  if (!process.exitCode) process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  for (const p of procs) { try { p.kill(); } catch {} }
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch {} }
}
