// Two-party room-join E2E (R106). The signed-in smoke covers one account; this
// drives the full join handshake through the UI with two real signed-in browser
// contexts (owner + joiner) against a live CSS pod: owner creates a room and
// shares the invite, joiner requests to join, owner approves the prompt, and the
// room appears for the joiner. This is the automated version of the multi-party
// part of the manual pass.
//
//   node web/smoke_web_join.mjs
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
  await signIn(pageB, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies });
  ok('both accounts signed in');

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

  // ── Joiner requests to join with the invite ──
  step = 'request';
  await pageB.evaluate(() => document.getElementById('join-room-btn').click());
  await pageB.waitForSelector('#join-room-input', { visible: true, timeout: 10000 });
  await pageB.type('#join-room-input', invite);
  await pageB.click('#join-room-submit-btn');
  ok('joiner sent a join request');

  // Force a drain + check, so the test does not depend on notification timing
  // (the live-subscription-vs-poll behaviour is exercised by the other smokes).
  const drainUntil = async (page, check, tries = 12) => {
    for (let i = 0; i < tries; i++) {
      try { await page.evaluate(() => window.proxionWebJoin && window.proxionWebJoin.drainOnce()); } catch { /**/ }
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
