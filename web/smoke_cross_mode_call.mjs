// Cross-mode call smoke: Proxion desktop (a gateway with a pod) and the Proxion
// browser build calling each other, signaled through their pods' call-inboxes.
//
// A throwaway CSS holds two accounts. Alice runs the desktop client: a gateway
// connected to her pod (only inside this smoke, pointed at the throwaway CSS).
// Bob runs the browser build, signed in to his pod. Media is Chrome's fake
// camera and microphone. Checks:
//   - Alice calls Bob; Bob answers; both sides read Verified (each fingerprint
//     signer is the one the other's signer.json names); hangup ends both sides
//   - Bob calls Alice; same checks
//   - after each hangup both call-inboxes are empty
//   - an invite "from Bob" signed by a key his pod does not name is never Verified
//
//   node web/smoke_cross_mode_call.mjs
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
  const dataDir = mkdtempSync(join(tmpdir(), 'proxion-xcall-'));
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

// Keep every toast text, since toasts expire before a slow check reads them.
const captureToasts = () => {
  window.__toasts = [];
  new MutationObserver(() => {
    document.querySelectorAll('#toast-container .toast__msg').forEach((el) => {
      const n = el.parentElement?.querySelector('.toast__count')?.textContent || '';
      if (el.__seen !== n) { el.__seen = n; window.__toasts.push(el.textContent); }
    });
  }).observe(document, { childList: true, subtree: true, characterData: true });
};

const until = (pg, fn, arg, ms = 30000) => pg.waitForFunction(fn, { timeout: ms, polling: 300 }, arg).then(() => true).catch(() => false);
const openThread = (page, webid) => page.evaluate((w) => {
  document.getElementById('nav-' + w)?.querySelector('.dm-item-body')?.click();
}, webid);
const ringing = (pg, ms) => until(pg, () => { const b = document.getElementById('voice-banner'); return b && getComputedStyle(b).display !== 'none'; }, null, ms);
const verifyState = (pg) => pg.evaluate(() => document.getElementById('vw-verified')?.dataset.state || 'none');
const connected = (pg, ms) => until(pg, () => /\d\d:\d\d/.test(document.getElementById('vw-status')?.textContent || ''), null, ms);
const widgetGone = (pg, ms) => until(pg, () => { const w = document.getElementById('voice-widget'); return !w || getComputedStyle(w).display === 'none'; }, null, ms);

// The browser build is nudged to read its call-inbox while waiting, so the
// smoke does not depend on the CSS notification channel's timing.
async function untilWithNudge(pg, fn, arg, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await pg.evaluate(fn, arg).catch(() => false)) return true;
    await pg.evaluate(() => window.proxionWebCalls && window.proxionWebCalls.drainOnce()).catch(() => {});
    await sleep(700);
  }
  return false;
}

// Is a call-inbox empty (read as its owner)?
async function inboxEmpty(session, root) {
  const r = await session.fetch(root + 'proxion/call-inbox/', { headers: { Accept: 'text/turtle' } });
  const ttl = await r.text();
  return r.ok && !/ldp:contains|#contains>/.test(ttl);
}
async function bothDrained(a, b) {
  for (let i = 0; i < 20; i++) {
    const [ea, eb] = [await inboxEmpty(a.session, a.root), await inboxEmpty(b.session, b.root)];
    if (ea && eb) return true;
    await sleep(1000);
  }
  return false;
}

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
  const boxes = [
    { session: await makeAuthSession(alice), root: alice.storageRoot },
    { session: await makeAuthSession(bob), root: bob.storageRoot },
  ];
  ok('CSS up, Alice (desktop) and Bob (browser build) provisioned');

  step = 'gateway';
  const A = await startGateway(css.url, alice);
  ok('Alice\'s gateway is up and connected to her pod');

  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors',
      '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
      '--autoplay-policy=no-user-gesture-required'],
  });
  const ctxA = await browser.createBrowserContext();
  const ctxB = await browser.createBrowserContext();
  const pageA = await ctxA.newPage();
  const pageB = await ctxB.newPage();
  const errs = [];
  pageA.on('pageerror', (e) => errs.push('A: ' + e.message));
  pageB.on('pageerror', (e) => errs.push('B: ' + e.message));
  await pageA.evaluateOnNewDocument(captureToasts);
  await pageB.evaluateOnNewDocument(captureToasts);

  // Every signal Alice sends goes to Bob's pod, none to the gateway's voice path.
  const aliceVoiceToGateway = [];
  const cdp = await pageA.createCDPSession();
  await cdp.send('Network.enable');
  cdp.on('Network.webSocketFrameSent', (e) => {
    try { const m = JSON.parse(e.response.payloadData); if (/^(voice_|ice_candidate)/.test(m.cmd || '')) aliceVoiceToGateway.push(m.cmd); } catch { /**/ }
  });

  step = 'alice-open';
  await pageA.evaluateOnNewDocument(() => {
    try { localStorage.setItem('proxion_display_name', 'Alice'); localStorage.setItem('proxion_pod_setup_skipped', '1'); } catch { /**/ }
  });
  await pageA.goto(A.url, { waitUntil: 'load', timeout: 30000 });
  await pageA.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  if (await until(pageA, () => window.proxionCrossMode && window.proxionCrossMode.isReady(), undefined, 60000)) ok('Alice\'s pod bridge is ready');
  else throw new Error('pod bridge never ready: ' + JSON.stringify(await pageA.evaluate(() => window.proxionCrossMode?.state())) + '\n' + gw._log().slice(-1500));
  const aliceSigner = await (await fetch(alice.storageRoot + 'proxion/identity/signer.json')).json().catch(() => null);

  step = 'bob-signin';
  await signIn(pageB, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies, name: 'Bob' });
  await sleep(2500);
  ok('Bob signed in to the browser build');

  step = 'contacts';
  // Each adds the other by WebID (only contacts ring the desktop).
  for (const [page, webid] of [[pageA, bob.webId], [pageB, alice.webId]]) {
    await page.evaluate(() => document.getElementById('add-peer-btn').click());
    await page.waitForSelector('#add-peer-input', { visible: true, timeout: 10000 });
    await page.type('#add-peer-input', webid);
    await page.click('#add-peer-submit-btn');
    await page.waitForFunction((w) => !!document.getElementById('nav-' + w), { timeout: 20000 }, webid);
    await openThread(page, webid);
  }
  ok('Alice and Bob added each other by WebID');

  // ── Alice (desktop) calls Bob (browser) ──────────────────────────────────
  step = 'alice-calls';
  if (!(await until(pageA, () => { const b = document.getElementById('start-call-btn'); return b && b.offsetParent !== null; }, null, 10000))) {
    throw new Error('Alice has no call button in the WebID thread');
  }
  await pageA.evaluate(() => document.getElementById('start-call-btn').click());
  if (await untilWithNudge(pageB, () => { const b = document.getElementById('voice-banner'); return b && getComputedStyle(b).display !== 'none'; }, null, 30000)) ok('Alice\'s call rings on Bob\'s browser');
  else throw new Error('Alice\'s call never rang on Bob');
  await pageB.evaluate(() => document.getElementById('voice-answer').click());
  const up1 = await Promise.all([connected(pageA, 40000), untilWithNudge(pageB, () => /\d\d:\d\d/.test(document.getElementById('vw-status')?.textContent || ''), null, 40000)]);
  if (up1[0] && up1[1]) ok('the call connected on both sides');
  else fail(`call never connected: alice=${up1[0]} bob=${up1[1]}`);
  await sleep(1500);
  const v1 = [await verifyState(pageA), await verifyState(pageB)];
  if (v1[0] === 'verified' && v1[1] === 'verified') ok('both sides read Verified (signers from signer.json)');
  else fail(`verify state: alice=${v1[0]} bob=${v1[1]}`);
  if (!aliceVoiceToGateway.length) ok('Alice\'s signals went through the pods, not the gateway voice path');
  else fail('signals sent to the gateway: ' + aliceVoiceToGateway.join(', '));

  step = 'alice-hangs-up';
  await pageA.evaluate(() => document.getElementById('end-call').click());
  const down1 = await Promise.all([widgetGone(pageA, 15000), untilWithNudge(pageB, () => { const w = document.getElementById('voice-widget'); return !w || getComputedStyle(w).display === 'none'; }, null, 20000)]);
  if (down1[0] && down1[1]) ok('hangup ended the call on both sides');
  else fail(`after hangup: alice ended=${down1[0]} bob ended=${down1[1]}`);
  if (await bothDrained(...boxes)) ok('both call-inboxes are empty');
  else fail('a call-inbox still holds signals after the hangup');

  // ── Bob (browser) calls Alice (desktop) ──────────────────────────────────
  step = 'bob-calls';
  await sleep(1500);
  await pageB.evaluate(() => document.getElementById('start-call-btn').click());
  if (await ringing(pageA, 30000)) ok('Bob\'s call rings on Alice\'s desktop');
  else throw new Error('Bob\'s call never rang on Alice');
  await pageA.evaluate(() => document.getElementById('voice-answer').click());
  const up2 = await Promise.all([connected(pageA, 40000), untilWithNudge(pageB, () => /\d\d:\d\d/.test(document.getElementById('vw-status')?.textContent || ''), null, 40000)]);
  if (up2[0] && up2[1]) ok('the call connected on both sides');
  else fail(`call never connected: alice=${up2[0]} bob=${up2[1]}`);
  await sleep(1500);
  const v2 = [await verifyState(pageA), await verifyState(pageB)];
  if (v2[0] === 'verified' && v2[1] === 'verified') ok('both sides read Verified');
  else fail(`verify state: alice=${v2[0]} bob=${v2[1]}`);

  step = 'bob-hangs-up';
  await pageB.evaluate(() => document.getElementById('end-call').click());
  const down2 = await Promise.all([widgetGone(pageA, 20000), widgetGone(pageB, 15000)]);
  if (down2[0] && down2[1]) ok('hangup ended the call on both sides');
  else fail(`after hangup: alice ended=${down2[0]} bob ended=${down2[1]}`);
  if (await bothDrained(...boxes)) ok('both call-inboxes are empty');
  else fail('a call-inbox still holds signals after the hangup');

  // ── A forged invite "from Bob" ───────────────────────────────────────────
  // Anyone may append to Alice's call-inbox. A self-consistent signature by a
  // key Bob's pod does not name must not read Verified (or connect).
  step = 'forged';
  await sleep(1500);
  const FP = 'sha-256 ' + Array.from({ length: 32 }, (_, i) => (i * 7 % 256).toString(16).padStart(2, '0').toUpperCase()).join(':');
  const sdp = `v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\na=ice-ufrag:abcd\r\na=ice-pwd:abcdefghijklmnopqrstuvwx\r\na=fingerprint:${FP}\r\na=setup:actpass\r\na=sendrecv\r\na=rtcp-mux\r\na=rtpmap:111 opus/48000/2\r\n`;
  // Signed with the app's own callsec.js (an ES module the gateway serves), by a
  // fresh key in a separate page.
  const forgePage = await (await browser.createBrowserContext()).newPage();
  await forgePage.goto(A.url + 'callsec.js', { waitUntil: 'load', timeout: 20000 });
  const { forgerDid, fp_sig } = await forgePage.evaluate(async (fp, to) => {
    const m = await import(location.href);
    const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
    const did = m.ed25519PubToDid(new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey)));
    return { forgerDid: did, fp_sig: await m.signFingerprint({ fingerprint: fp, role: 'offer', privKey: kp.privateKey, recipientDid: to }) };
  }, FP, alice.webId);
  await forgePage.close();
  const forged = await fetch(alice.storageRoot + 'proxion/call-inbox/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'voice_invite', from_webid: bob.webId, caller_webid: bob.webId, display_name: 'Bob',
      session_id: 'forged-' + stamp, sdp_offer: sdp, fp_sig, fp_signer: forgerDid }),
  });
  if (!forged.ok) fail('could not drop the forged invite: ' + forged.status);
  if (forgerDid === (aliceSigner && aliceSigner.signer)) fail('forger key collided');
  if (await ringing(pageA, 30000)) {
    await pageA.evaluate(() => document.getElementById('voice-answer').click());
    await sleep(3000);
    const vf = await verifyState(pageA);
    const refused = await pageA.evaluate(() => (window.__toasts || []).some((x) => /could not verify/i.test(x)));
    const up = await pageA.evaluate(() => /\d\d:\d\d/.test(document.getElementById('vw-status')?.textContent || ''));
    if (vf !== 'verified' && refused && !up) ok('a forged signer is refused, never Verified');
    else fail(`forged invite: state=${vf} refused=${refused} connected=${up}`);
    await pageA.evaluate(() => { const b = document.getElementById('end-call'); if (b && b.offsetParent) b.click(); });
  } else fail('the forged invite never reached Alice (it should ring, then be refused)');

  if (errs.length) fail('page errors:\n    ' + errs.slice(0, 6).join('\n    '));
  else ok('no uncaught page errors on either side');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
