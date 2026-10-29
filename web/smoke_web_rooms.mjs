// Shared rooms in the browser build, past the first join: three real accounts
// on a throwaway CSS pod. Covers what smoke_web_join does not: an empty room and
// its invite link survive the owner's reload, two simultaneous join requests are
// both prompted, a decline reaches the joiner, a joined room survives the
// joiner's reload, and each side sees the other's new message live.
//
//   node web/smoke_web_rooms.mjs
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

// Kick a join-inbox drain (not awaited: a drain waits on an open prompt) and
// poll `check` until it holds.
async function drainUntil(page, check, arg, tries = 16) {
  for (let i = 0; i < tries; i++) {
    try { await page.evaluate(() => { if (window.proxionWebJoin) window.proxionWebJoin.drainOnce(); }); } catch { /**/ }
    if (await page.evaluate(check, arg).catch(() => false)) return true;
    await sleep(1500);
  }
  return false;
}
const promptOpen = () => { const m = document.getElementById('confirm-modal'); return !!m && getComputedStyle(m).display !== 'none'; };
const hasRoom = (id) => !!document.getElementById('nav-' + id);
const seesText = (txt) => Array.from(document.querySelectorAll('#message-feed .message')).some((el) => (el.textContent || '').includes(txt));

async function waitFor(page, fn, arg, ms) {
  return page.waitForFunction(fn, { timeout: ms, polling: 500 }, arg).then(() => true).catch(() => false);
}
async function reload(page) {
  await page.reload({ waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(() => typeof window.proxionWebDm === 'object', { timeout: 30000 });
}
async function openRoom(page, id) {
  await page.evaluate((rid) => document.getElementById('nav-' + rid)?.click(), id);
  await waitFor(page, () => !!document.getElementById('message-input') && !document.getElementById('message-input').disabled, null, 10000);
}
async function send(page, text) {
  await page.focus('#message-input');
  await page.type('#message-input', text);
  await page.keyboard.press('Enter');
}
async function requestJoin(page, invite) {
  await page.evaluate(() => document.getElementById('join-room-btn').click());
  await page.waitForSelector('#join-room-input', { visible: true, timeout: 10000 });
  await page.type('#join-room-input', invite);
  await page.click('#join-room-submit-btn');
  return waitFor(page, () => document.getElementById('join-pending-banner')?.classList.contains('visible'), null, 15000);
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
  const carol = await provisionAccount(css.url, { email: `carol-${stamp}@test.example`, password: 'pw-12345678', label: 'carol' });
  ok('CSS pod up + three accounts provisioned');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  const pageA = await (await browser.createBrowserContext()).newPage();   // owner
  const pageB = await (await browser.createBrowserContext()).newPage();   // joiner
  const pageC = await (await browser.createBrowserContext()).newPage();   // joiner
  const errs = [];
  for (const [tag, p] of [['A', pageA], ['B', pageB], ['C', pageC]]) p.on('pageerror', (e) => errs.push(`${tag}: ${e.message}`));

  step = 'signin';
  await signIn(pageA, { appOrigin: served.origin, cssUrl: css.url, cookies: alice.cookies });
  await signIn(pageB, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies });
  await signIn(pageC, { appOrigin: served.origin, cssUrl: css.url, cookies: carol.cookies });
  ok('three accounts signed in');

  // ── Owner creates a room, sends nothing, reloads ──
  step = 'create';
  const roomName = 'rooms-' + Math.random().toString(36).slice(2, 7);
  await pageA.evaluate(() => document.getElementById('create-room-btn').click());
  await pageA.waitForSelector('#room-name-input', { visible: true, timeout: 10000 });
  await pageA.type('#room-name-input', roomName);
  await pageA.click('#room-create-submit');
  await pageA.waitForFunction(() => (document.getElementById('room-invite-url')?.textContent || '').includes('?join='), { timeout: 15000 });
  const invite = await pageA.$eval('#room-invite-url', (el) => el.textContent.trim());
  const roomId = decodeURIComponent(new URL(invite).searchParams.get('join').split('~')[0]);
  if (new URL(invite).searchParams.get('mode') === 'web') ok('the invite link keeps mode=web');
  else fail(`invite has no mode=web: ${invite}`);
  await sleep(2000);   // let the descriptor + type-index writes land

  step = 'owner-reload';
  await reload(pageA);
  if (await waitFor(pageA, hasRoom, roomId, 20000)) ok('an empty room survives the owner\'s reload');
  else fail('the empty room was gone after the owner reloaded');
  await openRoom(pageA, roomId);
  const inviteAfter = await pageA.evaluate(() => {
    const b = document.getElementById('invite-btn');
    return !!b && b.style.display !== 'none';
  });
  if (inviteAfter) ok('after reload the owner still has the invite link');
  else fail('the invite link was missing after the owner reloaded');

  // ── Two join requests at once ──
  step = 'requests';
  const sentB = await requestJoin(pageB, invite);
  const sentC = await requestJoin(pageC, invite);
  if (sentB && sentC) ok('both joiners sent a request (pending banner shown)');
  else fail(`request banners: bob=${sentB} carol=${sentC}`);

  step = 'pending-reload';
  await reload(pageC);
  if (await waitFor(pageC, () => document.getElementById('join-pending-banner')?.classList.contains('visible'), null, 15000)) ok('the pending banner survives the joiner\'s reload');
  else fail('the pending banner was gone after reload');

  step = 'prompts';
  const prompts = [];
  if (await drainUntil(pageA, promptOpen)) {
    prompts.push(await pageA.$eval('#confirm-msg', (el) => el.textContent));
    await pageA.click('#confirm-ok');
  }
  await sleep(800);
  if (await drainUntil(pageA, promptOpen)) {
    prompts.push(await pageA.$eval('#confirm-msg', (el) => el.textContent));
    await pageA.click('#confirm-cancel');
  }
  if (prompts.length === 2) ok('both join requests were prompted, one at a time');
  else fail(`only ${prompts.length} prompt(s): ${JSON.stringify(prompts)}`);
  if (prompts.some((p) => /https?:\/\//.test(p))) fail(`a prompt shows a raw WebID: ${JSON.stringify(prompts)}`);
  else ok('prompts name the requester without a raw WebID');
  const firstIsBob = /bob/i.test(prompts[0] || '');
  const [joiner, declined] = firstIsBob ? [pageB, pageC] : [pageC, pageB];

  step = 'decline';
  const cleared = await drainUntil(declined, () => !document.getElementById('join-pending-banner')?.classList.contains('visible'));
  if (cleared) ok('the decline reached the declined joiner (banner cleared)');
  else fail('the declined joiner kept waiting');

  step = 'joined';
  if (await drainUntil(joiner, hasRoom, roomId)) ok('the approved joiner got the room');
  else fail('the room never appeared for the approved joiner');

  step = 'joiner-reload';
  await reload(joiner);
  if (await waitFor(joiner, hasRoom, roomId, 20000)) ok('the joined room survives the joiner\'s reload');
  else fail('the joined room was gone after the joiner reloaded');

  // ── Live delivery both ways, with the room open on both sides ──
  step = 'live';
  await openRoom(joiner, roomId);
  await openRoom(pageA, roomId);
  const fromJoiner = 'from-joiner-' + Math.random().toString(36).slice(2, 7);
  await send(joiner, fromJoiner);
  if (await waitFor(pageA, seesText, fromJoiner, 15000)) ok('the owner saw the joiner\'s message within 15s');
  else fail('the owner never saw the joiner\'s message');
  const fromOwner = 'from-owner-' + Math.random().toString(36).slice(2, 7);
  await send(pageA, fromOwner);
  if (await waitFor(joiner, seesText, fromOwner, 15000)) ok('the joiner saw the owner\'s message within 15s');
  else fail('the joiner never saw the owner\'s message');

  // ── Members list, then the joiner leaves ──
  step = 'members';
  await pageA.evaluate(() => document.getElementById('members-toggle')?.click());
  if (await waitFor(pageA, () => document.querySelectorAll('#members-list .member-item').length >= 2, null, 15000)) ok('the owner\'s Members panel lists the owner and the joiner');
  else fail('the Members panel did not list the joiner');

  step = 'leave';
  await joiner.evaluate(() => document.getElementById('leave-room-btn')?.click());
  if (await waitFor(joiner, promptOpen, null, 5000)) await joiner.click('#confirm-ok');
  const left = await waitFor(joiner, (id) => !document.getElementById('nav-' + id), roomId, 15000);
  await sleep(1500);
  await reload(joiner);
  await sleep(4000);
  if (left && !(await joiner.evaluate(hasRoom, roomId))) ok('the joiner left the room, and it stays gone after reload');
  else fail(`leave: left=${left}, back after reload=${await joiner.evaluate(hasRoom, roomId)}`);
  const ownerLeave = await pageA.evaluate(() => getComputedStyle(document.getElementById('leave-room-btn')).display);
  if (ownerLeave === 'none') ok('the owner is offered Delete, not Leave');
  else fail('the owner is offered Leave room in the browser build');

  if (errs.length) fail('page errors:\n    ' + errs.slice(0, 6).join('\n    '));
  else ok('no uncaught page errors');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
