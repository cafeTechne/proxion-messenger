// Room voice channel smoke: three people (separate browser profiles) share one
// throwaway gateway and one room. Asserts what each side sees:
//
//   Join voice in the room header -> the others see who is in voice ->
//   a second person joins -> both hear each other (audio over a connected
//   peer connection) -> leave -> the count drops and the header offers Join again.
//
//   node web/smoke_voice_channel.mjs   (spawns its own gateway; needs python + chrome)
//
// Exit 0 = every step held, non-zero = a step failed.
import { existsSync } from 'fs';
import puppeteer from 'puppeteer-core';
import { smokeGateway } from './scripts/gw-harness.mjs';

const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find((p) => existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

const ROOM = 'voice-' + Math.random().toString(36).slice(2, 7);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let step = 'init';
const fail = (m) => { console.error(`  ✗ [${step}] ${m}`); process.exitCode = 1; };
const check = (ok, m) => { if (!ok) fail(m); };

async function client(browser, url, name) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  page._errs = [];
  page.on('pageerror', (e) => page._errs.push(`${name}: ${e.message}`));
  await page.evaluateOnNewDocument((n) => {
    localStorage.setItem('proxion_display_name', n);
    localStorage.setItem('proxion_pod_setup_skipped', '1');
  }, name);
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 });
  return page;
}
const click = (p, sel) => p.evaluate((s) => {
  const e = document.querySelector(s); if (!e) throw new Error('missing ' + s); e.click();
}, sel);
const shown = (p, sel) => p.evaluate((s) => {
  const e = document.querySelector(s);
  return !!e && !e.hidden && getComputedStyle(e).display !== 'none' && e.getClientRects().length > 0;
}, sel);
// polling: 200 because rAF polling never fires in a background tab.
const until = (p, fn, arg, ms = 10000) => p.waitForFunction(fn, { timeout: ms, polling: 200 }, arg).then(() => true, () => false);
const inList = (p, name) => until(p, (n) => [...document.querySelectorAll('#room-list li')].some((l) => l.textContent.includes(n)), name);
async function joinByLink(p, link) {
  await click(p, '#join-room-btn');
  await p.evaluate(() => { document.getElementById('join-room-input').value = ''; });
  await p.type('#join-room-input', link);
  await click(p, '#join-room-submit-btn');
}
const voiceCount = (p) => p.evaluate((n) => {
  const row = [...document.querySelectorAll('#room-list li')].find((l) => l.textContent.includes(n));
  const b = row && row.querySelector('.room-item-voice');
  return b ? parseInt(b.textContent.replace(/\D+/g, ''), 10) : 0;
}, ROOM);
// A peer shown as connected, whose media element carries live inbound audio.
const hearsSomeone = (p) => until(p, () => {
  const els = [...document.querySelectorAll('#voice-channel-videos [data-vc-webid]')];
  const live = els.some((a) => a.srcObject && a.srcObject.getAudioTracks().some((t) => t.readyState === 'live'));
  const pills = [...document.querySelectorAll('#voice-channel-participants [data-vc-webid]')];
  return live && pills.some((e) => /: connected$/.test(e.title));
}, null, 15000);

const gw = await smokeGateway();
const browser = await puppeteer.launch({
  executablePath: CHROME, headless: 'new',
  args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu', '--window-size=1280,900',
         '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
  defaultViewport: { width: 1280, height: 900 },
});
let pages = [];
try {
  step = 'connect';
  const alice = await client(browser, gw.url, 'Alice');
  const bob = await client(browser, gw.url, 'Bob');
  const carol = await client(browser, gw.url, 'Carol');
  pages = [alice, bob, carol];

  step = 'create-room';
  await click(alice, '#create-room-btn');
  await alice.type('#room-name-input', ROOM);
  await click(alice, '#room-create-submit');
  check(await until(alice, () => document.getElementById('room-invite-result')?.style.display !== 'none'), 'invite link never shown');
  const link = await alice.evaluate(() => document.getElementById('room-invite-url').textContent.trim());
  await click(alice, '#room-create-done-btn');
  await joinByLink(bob, link);
  check(await inList(bob, ROOM), 'Bob never got the room');
  await joinByLink(carol, link);
  check(await inList(carol, ROOM), 'Carol never got the room');
  await sleep(500);

  step = 'alice-joins';
  check(await shown(alice, '#join-voice-btn'), 'no Join voice button in the room header');
  await click(alice, '#join-voice-btn');
  check(await until(alice, () => getComputedStyle(document.getElementById('leave-voice-channel-btn')).display !== 'none'),
    'Leave channel never appeared after joining');
  check(!(await shown(alice, '#join-voice-btn')), 'Join voice still shown while in the channel');
  for (const [p, who] of [[bob, 'Bob'], [carol, 'Carol']]) {
    check(await until(p, () => document.getElementById('join-voice-count')?.textContent === '1'),
      `${who}'s Join voice button does not show 1 person in voice`);
    check((await voiceCount(p)) === 1, `${who}'s sidebar row does not show 1 in voice`);
  }

  step = 'bob-joins';
  await click(bob, '#join-voice-btn');
  check(await hearsSomeone(alice), 'Alice never received Bob\'s audio');
  check(await hearsSomeone(bob), 'Bob never received Alice\'s audio');
  check(await until(alice, () => document.querySelectorAll('#voice-channel-participants [data-vc-webid]').length === 1),
    'Alice\'s channel panel does not list Bob');
  check(await until(alice, () => document.getElementById('voice-channel-participants').textContent.includes('Bob')),
    'Alice\'s channel panel names Bob by a key instead of his name');
  check(await until(carol, () => document.getElementById('join-voice-count')?.textContent === '2'),
    'Carol does not see 2 people in voice');

  step = 'camera';
  const tileShown = (p) => p.evaluate(() => [...document.querySelectorAll('#voice-channel-videos [data-vc-webid]')]
    .some((e) => getComputedStyle(e).display !== 'none'));
  check(!(await tileShown(bob)), 'voice-only call shows a black video tile');
  await click(alice, '#voice-channel-camera-btn');
  check(await until(alice, () => document.getElementById('voice-channel-camera-btn').getAttribute('aria-pressed') === 'true'),
    'camera button does not show it is on');
  check(await until(bob, () => [...document.querySelectorAll('#voice-channel-videos [data-vc-webid]')]
    .some((e) => getComputedStyle(e).display !== 'none' && e.videoWidth > 0)), "Bob never saw Alice's camera");
  await click(alice, '#voice-channel-camera-btn');

  step = 'leave';
  await click(bob, '#leave-voice-channel-btn');
  check(await until(bob, () => getComputedStyle(document.getElementById('join-voice-btn')).display !== 'none'),
    'Bob is not offered Join voice again after leaving');
  check(await until(carol, () => document.getElementById('join-voice-count')?.textContent === '1'),
    'Carol still counts Bob in voice after he left');
  check(await until(alice, () => document.querySelectorAll('#voice-channel-participants [data-vc-webid]').length === 0),
    'Alice\'s panel still lists Bob after he left');
  await click(alice, '#leave-voice-channel-btn');
  check(await until(carol, () => document.getElementById('join-voice-count')?.hidden === true),
    'Carol still sees people in voice after everyone left');
  check(await until(carol, async () => !document.querySelector('#room-list .room-item-voice')),
    'Carol\'s sidebar still shows a voice badge after everyone left');

  step = 'done';
  const errs = pages.flatMap((p) => p._errs);
  if (errs.length) fail('page errors:\n   - ' + errs.join('\n   - '));
  if (!process.exitCode) console.log(`  ✓ voice channel OK: join from the header, others see who is in voice, two-way audio, leave ("${ROOM}")`);
} catch (e) {
  fail(`threw: ${e.message}`);
} finally {
  await browser.close().catch(() => {});
  await gw.stop();
}
