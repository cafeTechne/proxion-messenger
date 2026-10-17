// Two tabs of one account smoke: only one tab may run the DM ratchet.
//
// Alice@A and Bob@B become friends and DM. Bob then opens a second tab in the
// same browser profile. Before tablock.js both tabs ran the ratchet, forked it,
// and Alice saw "[could not decrypt]" while each Bob tab showed the other's sends
// as ciphertext. Now the second tab waits behind "open in another tab", takes
// over with "Use here" (the first tab steps down), and every message in both
// directions stays readable across two takeovers.
//
//   node web/smoke_tabs.mjs    (spawns both gateways; needs python + chrome)
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
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
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

const procs = [], dirs = [];
async function startGateway(name) {
  const httpPort = await freePort(), wsPort = await freePort();
  const dataDir = mkdtempSync(join(tmpdir(), `proxion-tabs-${name}-`));
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
  p.stdout.on('data', d => log += d); p.stderr.on('data', d => log += d);
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && !/PROXION_GATEWAY_READY/.test(log)) {
    if (p.exitCode !== null) throw new Error(`${name} gateway exited:\n${log.slice(-600)}`);
    await sleep(300);
  }
  if (!/PROXION_GATEWAY_READY/.test(log)) throw new Error(`${name} not ready:\n${log.slice(-600)}`);
  await waitForPort(httpPort, 15000);
  return { url: `http://127.0.0.1:${httpPort}/` };
}

const online = (p, ms = 20000) => p.waitForFunction(
  () => document.querySelector('.dot')?.classList.contains('online') && !document.getElementById('tab-notice'),
  { timeout: ms }).then(() => true, () => false);
const hasNotice = (p, ms = 15000) => p.waitForFunction(
  () => !!document.getElementById('tab-notice'), { timeout: ms }).then(() => true, () => false);

async function openTab(ctx, url, name, { expectNotice = false } = {}) {
  const page = await ctx.newPage();
  page._errs = []; page._con = [];
  page.on('console', m => page._con.push(`${name}: ${m.text().slice(0, 200)}`));
  page.on('pageerror', e => page._errs.push(`${name}: ${e.message}`));
  await page.evaluateOnNewDocument((n) => {
    try { localStorage.setItem('proxion_display_name', n); localStorage.setItem('proxion_pod_setup_skipped', '1'); } catch { /* storage off */ }
  }, name);
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  if (expectNotice) return page;
  await page.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  if (!await online(page)) throw new Error(`${name} never came online`);
  return page;
}

async function befriend(alice, bob) {
  await bob.evaluate(() => document.getElementById('settings-btn')?.click());
  const addr = await bob.waitForFunction(() => window.proxionAddress || localStorage.getItem('proxion_my_address'), { timeout: 10000 }).then(h => h.jsonValue());
  await bob.evaluate(() => { const m = document.getElementById('settings-modal'); if (m) m.style.display = 'none'; });
  await alice.evaluate(() => document.getElementById('add-peer-btn').click());
  await sleep(300);
  await alice.type('#add-peer-input', addr);
  await alice.evaluate(() => document.getElementById('add-peer-submit-btn').click());
  await bob.waitForFunction(() => document.querySelector('#friend-request-list li [data-fr-action="accept"]'), { timeout: 20000 });
  await bob.evaluate(() => document.querySelector('#friend-request-list li [data-fr-action="accept"]').click());
  for (const p of [bob, alice]) await p.waitForFunction(() => document.querySelector('#contacts-list li, #dm-list li'), { timeout: 20000 });
  await sleep(800);
}
async function openDm(p) {
  const has = await p.waitForFunction(() => document.querySelector('#dm-list li, #contacts-list li'), { timeout: 15000 }).then(() => true, () => false);
  if (!has) throw new Error('no DM entry: ' + await p.evaluate(() => JSON.stringify({ dm: document.getElementById('dm-list')?.innerHTML.slice(0, 300), contacts: document.getElementById('contacts-list')?.innerHTML.slice(0, 300), feed: document.getElementById('message-feed')?.textContent.slice(0, 200) })));
  await p.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await p.evaluate(() => document.querySelector('#dm-list li, #contacts-list li').click());
  await p.waitForFunction(() => { const h = document.getElementById('chat-header-name'); return h && !/welcome/i.test(h.textContent || ''); }, { timeout: 8000 });
  await sleep(500);
}
async function send(p, text) {
  await p.focus('#message-input');
  await p.type('#message-input', text);
  await p.keyboard.press('Enter');
}
const feedText = (p) => p.evaluate(() => [...document.querySelectorAll('#message-feed .message .msg-text')].map(m => m.textContent));
const sees = (p, text, ms = 15000) => p.waitForFunction(
  (t) => [...document.querySelectorAll('#message-feed .message .msg-text')].some(m => m.textContent.includes(t)),
  { timeout: ms }, text).then(() => true, () => false);

let step = 'init', browser = null;
const fail = (m) => { console.error(`  x [${step}] ${m}`); process.exitCode = 1; };
const ok = (m) => console.log(`  ok ${m}`);
// Plaintext only: no placeholder, and nothing that looks like a sealed payload.
async function clean(p, who) {
  const f = await feedText(p);
  const bad = f.filter(t => /could not decrypt|decryption error/i.test(t) || /^[A-Za-z0-9_-]{24,}$/.test(t.trim()));
  if (bad.length) fail(`${who} feed has unreadable messages: ${JSON.stringify(bad)}`);
}
async function takeover(p, who) {
  await p.bringToFront();   // the user switches to the tab, then clicks
  await p.evaluate(() => document.getElementById('tab-notice-use-here').click());
  if (!await online(p, 25000)) {
    fail(`${who} did not come online after Use here`);
    console.error(p._con.slice(-25).join('\n'));
    return false;
  }
  await openDm(p);
  return true;
}

try {
  step = 'spawn';
  const A = await startGateway('a'); const B = await startGateway('b');
  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'] });
  const alice = await openTab(await browser.createBrowserContext(), A.url, 'Alice');
  const bobCtx = await browser.createBrowserContext();
  const bob1 = await openTab(bobCtx, B.url, 'Bob');

  step = 'befriend';
  await befriend(alice, bob1);
  await openDm(alice); await openDm(bob1);
  await send(alice, 'hello-1');
  if (!await sees(bob1, 'hello-1')) fail('bob tab 1 did not get the first DM');

  step = 'second-tab';
  const bob2 = await openTab(bobCtx, B.url, 'Bob', { expectNotice: true });
  if (!await hasNotice(bob2)) fail('second tab did not show the "open in another tab" notice');
  else ok('second tab waits behind the notice');
  if (await bob2.evaluate(() => document.querySelector('.dot')?.classList.contains('online'))) fail('waiting tab connected to the gateway');

  step = 'takeover-1';
  if (await takeover(bob2, 'bob tab 2')) ok('Use here: tab 2 is active');
  if (!await hasNotice(bob1)) fail('tab 1 did not step down to the notice');
  await send(alice, 'hello-2');
  if (!await sees(bob2, 'hello-2')) fail('bob tab 2 did not decrypt a DM after takeover');
  await send(bob2, 'reply-from-tab2');
  if (!await sees(alice, 'reply-from-tab2')) fail('alice did not get tab 2 reply');

  step = 'takeover-2';
  if (await takeover(bob1, 'bob tab 1')) ok('Use here back: tab 1 is active');
  if (!await hasNotice(bob2)) fail('tab 2 did not step down to the notice');
  await send(alice, 'hello-3');
  if (!await sees(bob1, 'hello-3')) fail('bob tab 1 did not decrypt a DM after taking back over');
  for (const m of ['hello-1', 'hello-2']) if (!await sees(bob1, m, 3000)) fail(`bob tab 1 lost ${m} from history`);
  await send(bob1, 'reply-from-tab1');
  if (!await sees(alice, 'reply-from-tab1')) fail('alice did not get tab 1 reply');
  await sleep(1500);

  step = 'readable';
  await clean(alice, 'alice'); await clean(bob1, 'bob tab 1');
  const errs = [...alice._errs, ...bob1._errs, ...bob2._errs];
  if (errs.length) fail(`page errors: ${errs.join(' | ')}`);
  if (!process.exitCode) console.log('PASS: one active tab, takeovers keep every DM readable');
} catch (e) {
  fail(e.stack || String(e));
} finally {
  try { if (browser) await browser.close(); } catch { /* gone */ }
  for (const p of procs) { try { p.kill(); } catch { /* gone */ } }
  await sleep(500);
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* locked */ } }
}
