// Invite-link join E2E. smoke_web_join pastes the invite into Join a room as a
// signed-in user; this one takes the path a new invitee takes: open the link
// signed out, sign in (an OIDC round-trip that drops ?join=), and still land in
// the room once the owner approves. A brand-new pod account has no display
// name, which used to hold the join forever.
//
//   node web/smoke_web_join_link.mjs
// Needs Chrome (or PROXION_CHROME), openssl, and npx (for CSS). Self-skips if the
// cert or CSS cannot be produced. Slower than the other smokes because the
// handshake travels through pod-notification polling.

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

let browser = null, served = null, css = null;
async function cleanup() {
  try { if (browser) await browser.close(); } catch { /**/ }
  try { if (served) served.server.close(); } catch { /**/ }
  try { if (css) css.stop(); } catch { /**/ }
}

try {
  step = 'serve';
  served = await buildAndServeHttps(HERE, join(HERE, 'scripts', 'build-web.mjs'));
  if (!served) { console.log('  \u2013 skipped: could not build/serve over https (openssl?).'); await cleanup(); process.exit(0); }
  ok('built + serving the web app over https');

  step = 'css';
  css = await startCss({ allowSelfSignedClientId: true }).catch(() => null);
  if (!css) { console.log('  \u2013 skipped: no CSS pod available (offline / npx blocked).'); await cleanup(); process.exit(0); }
  const alice = await provisionAccount(css.url, { email: `alice-${Date.now()}@test.example`, password: 'pw-12345678', label: 'alice' });
  const bob = await provisionAccount(css.url, { email: `bob-${Date.now()}@test.example`, password: 'pw-12345678', label: 'bob' });
  ok('CSS pod up + two accounts provisioned');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  // Separate browser contexts so the two accounts have isolated cookies + sessions.
  const ctxA = await browser.createBrowserContext();
  const ctxB = await browser.createBrowserContext();
  const pageA = await ctxA.newPage();   // owner (Alice)
  const pageB = await ctxB.newPage();   // joiner (Bob)
  const errsA = [], errsB = [];
  pageA.on('pageerror', (e) => errsA.push('A: ' + e.message));
  pageB.on('pageerror', (e) => errsB.push('B: ' + e.message));
  const diag = (tag) => (m) => { const tx = m.text(); if (/\[diag\]|\[webjoin\]|\[pod\]/.test(tx)) console.log(`    ${tag} ${tx}`); };
  pageA.on('console', diag('A>'));
  pageB.on('console', diag('B>'));

  step = 'signin';
  await signIn(pageA, { appOrigin: served.origin, cssUrl: css.url, cookies: alice.cookies });
  ok('owner signed in');

  // ── Owner creates a room and grabs the invite ──
  step = 'create';
  const roomName = 'join-' + Math.random().toString(36).slice(2, 7);
  await pageA.evaluate(() => document.getElementById('create-room-btn').click());
  await pageA.waitForSelector('#room-name-input', { visible: true, timeout: 10000 });
  await pageA.type('#room-name-input', roomName);
  await pageA.click('#room-create-submit');
  await pageA.waitForFunction(() => { const el = document.getElementById('room-invite-url'); return el && (el.textContent || '').includes('?join='); }, { timeout: 15000 });
  const invite = await pageA.$eval('#room-invite-url', (el) => el.textContent.trim());
  ok(`owner created the room, invite minted`);

  // ── Joiner opens the invite link while signed out, then signs in ──
  step = 'request';
  {
    const cssHost = new URL(css.url).hostname;
    for (const [name, value] of Object.entries(bob.cookies)) await pageB.setCookie({ name, value, domain: cssHost, path: '/' });
    await pageB.goto(invite, { waitUntil: 'networkidle2', timeout: 30000 });
    await new Promise((r) => setTimeout(r, 1500));
    const pre = await pageB.evaluate(() => ({
      stash: localStorage.getItem('proxion_pending_join'),
      notice: !document.getElementById('ob-invite-notice')?.hidden,
    }));
    if (pre.stash && !pre.notice) ok('signed out: the invite is held for after sign-in, no "other gateway" notice');
    else fail(`landing: ${JSON.stringify(pre)}`);
    await pageB.evaluate((u) => { window.proxionWebLogin(u); }, css.url);
    await pageB.waitForSelector('input[name="webId"]', { timeout: 25000 });
    await pageB.click('input[name="webId"]');
    await Promise.all([pageB.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => null), pageB.click('#authorize')]);
    await pageB.waitForFunction(() => !location.href.includes('/consent') && !location.href.includes('/.oidc/'), { timeout: 30000 });
    await pageB.waitForFunction(() => typeof window.proxionWebDm === 'object', { timeout: 20000 });
    const sent = await pageB.waitForFunction(() => !localStorage.getItem('proxion_pending_join')
      && document.getElementById('join-pending-banner')?.classList.contains('visible'), { timeout: 15000 })
      .then(() => true).catch(() => false);
    if (sent) ok('after sign-in the join request went out (pending banner shown)');
    else fail('the join request was never sent after sign-in');
  }

  // Force a drain + check, so the test does not depend on notification timing
  // (the live-subscription-vs-poll behaviour is exercised by the other smokes).
  const drainUntil = async (page, check, tries = 12) => {
    for (let i = 0; i < tries; i++) {
      try { await page.evaluate(() => { if (window.proxionWebJoin) window.proxionWebJoin.drainOnce(); }); } catch { /**/ }
      if (await page.evaluate(check).catch(() => false)) return true;
      await new Promise((r) => setTimeout(r, 1500));
    }
    return false;
  };

  // ── Owner drains the request, is prompted, and approves ──
  step = 'approve';
  const prompted = await drainUntil(pageA, () => { const m = document.getElementById('confirm-modal'); return !!m && getComputedStyle(m).display !== 'none'; });
  if (prompted) { await pageA.click('#confirm-ok'); ok('owner got the join prompt and approved'); }
  else fail('owner was never prompted to approve the join');

  // ── The joiner drains the approval and the room appears ──
  step = 'joined';
  await pageB.evaluate((n) => { window.__roomName = n; }, roomName);
  const joined = await drainUntil(pageB, () => Array.from(document.querySelectorAll('#room-list li, nav li')).some((el) => (el.textContent || '').includes(window.__roomName)), 16);
  if (joined) ok('the room appeared in the joiner\'s sidebar');
  else fail('the room never appeared for the joiner');

  if (errsA.length || errsB.length) fail('page errors:\n    ' + [...errsA, ...errsB].slice(0, 6).join('\n    '));
  else ok('no uncaught page errors on either side');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
