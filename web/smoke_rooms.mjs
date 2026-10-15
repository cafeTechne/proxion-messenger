// Room moderation smoke: three people (separate browser profiles, so separate
// identities) share one throwaway gateway and drive the room flows a new
// owner reaches for, asserting what each side sees:
//
//   create + invite link -> join by link -> member list updates live ->
//   leave clears the header -> ban / unban from the options menu ->
//   transfer ownership + accept -> webhook link and delete -> delete room.
//
//   node web/smoke_rooms.mjs          (spawns its own gateway; needs python + chrome)
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

const ROOM = 'rooms-' + Math.random().toString(36).slice(2, 7);
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
const until = (p, fn, arg, ms = 8000) => p.waitForFunction(fn, { timeout: ms }, arg).then(() => true, () => false);
const inList = (p, name) => until(p, (n) => [...document.querySelectorAll('#room-list li')].some((l) => l.textContent.includes(n)), name);
const memberNames = (p) => p.evaluate(() => [...document.querySelectorAll('#members-list .member-item')].map((e) => e.textContent));
async function joinByLink(p, link) {
  await click(p, '#join-room-btn');
  await p.evaluate(() => { document.getElementById('join-room-input').value = ''; });
  await p.type('#join-room-input', link);
  await click(p, '#join-room-submit-btn');
}
async function memberAction(owner, name, action) {
  await owner.evaluate((n) => {
    const it = [...document.querySelectorAll('#members-list .member-item')].find((e) => e.textContent.includes(n));
    if (!it) throw new Error('no member row for ' + n);
    const r = it.getBoundingClientRect();
    it.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 10, clientY: r.top + 5 }));
  }, name);
  await sleep(150);
  await click(owner, `#member-context-menu [data-role-action="${action}"]`);
}
const confirmOk = (p) => until(p, () => getComputedStyle(document.getElementById('confirm-modal')).display !== 'none')
  .then(() => click(p, '#confirm-ok'));

const gw = await smokeGateway();
const browser = await puppeteer.launch({
  executablePath: CHROME, headless: 'new',
  args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu', '--window-size=1280,900'],
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
  check(link.startsWith(new URL(gw.url).origin + '/?join='), `invite link not on this gateway: ${link}`);
  await click(alice, '#room-create-done-btn');
  await sleep(300);
  await click(alice, '#members-toggle');

  step = 'join-by-link';
  await joinByLink(bob, link);
  check(await inList(bob, ROOM), 'Bob never got the room');
  await joinByLink(carol, link);
  check(await inList(carol, ROOM), 'Carol never got the room');
  // The owner's open member list picks both up without reopening it.
  check(await until(alice, () => document.querySelectorAll('#members-list .member-item').length === 3, null, 6000),
    `owner's member list did not update live: ${JSON.stringify(await memberNames(alice))}`);

  step = 'leave';
  await click(carol, '#room-options-btn');
  await sleep(200);
  await click(carol, '#leave-room-btn');
  await confirmOk(carol);
  check(await until(carol, () => !document.getElementById('chat-header-name').textContent.includes('#')),
    'header still names the room after leaving');
  check(!(await shown(carol, '#invite-btn')) && !(await shown(carol, '#room-options-btn')),
    'room buttons still in the header after leaving');
  check(await until(alice, () => document.querySelectorAll('#members-list .member-item').length === 2, null, 6000),
    'owner still lists the member who left');
  await joinByLink(carol, link);
  check(await inList(carol, ROOM), 'Carol could not rejoin');
  check(await until(alice, () => document.querySelectorAll('#members-list .member-item').length === 3, null, 6000),
    'rejoin not reflected in the owner list');

  step = 'ban-unban';
  await memberAction(alice, 'Carol', 'ban');
  await confirmOk(alice);
  check(await until(carol, () => !document.getElementById('chat-header-name').textContent.includes('#')),
    'banned member still sees the room header');
  await sleep(500);
  await joinByLink(carol, link);
  check(await until(carol, () => document.getElementById('join-room-error').textContent.trim().length > 0),
    'banned member got no error when rejoining');
  await click(carol, '#join-room-cancel-btn');
  await click(alice, '#room-options-btn');
  await sleep(200);
  check(await shown(alice, '#room-opt-bans'), 'owner has no Banned members item');
  await click(alice, '#room-opt-bans');
  check(await until(alice, () => !!document.querySelector('#room-bans-list [data-unban-did]')), 'bans list never filled');
  await click(alice, '#room-bans-list [data-unban-did]');
  await click(alice, '#room-bans-close');
  await sleep(300);
  await joinByLink(carol, link);
  check(await inList(carol, ROOM), 'unbanned member could not rejoin');
  await click(bob, '#room-options-btn');
  await sleep(200);
  check(!(await shown(bob, '#room-opt-bans')), 'a plain member sees Banned members');
  await bob.keyboard.press('Escape');

  step = 'transfer-ownership';
  await sleep(500);
  await memberAction(alice, 'Bob', 'transfer');
  check(await until(bob, () => getComputedStyle(document.getElementById('confirm-modal')).display !== 'none'),
    'Bob never got the ownership offer');
  await click(bob, '#confirm-ok');
  check(await until(bob, () => document.getElementById('integrations-btn').style.display !== 'none'),
    'new owner has no integrations button');
  check(await until(alice, () => document.getElementById('integrations-btn').style.display === 'none'),
    'old owner keeps the integrations button');

  step = 'webhook';
  await click(bob, '#integrations-btn');
  await until(bob, () => !!document.getElementById('ci-incoming-btn'));
  await click(bob, '#ci-incoming-btn');
  await until(bob, () => !!document.getElementById('prompt-ok'));
  await click(bob, '#prompt-ok');
  check(await until(bob, () => getComputedStyle(document.getElementById('webhook-created-modal')).display !== 'none'),
    'webhook link never shown');
  const hook = await bob.evaluate(() => document.getElementById('webhook-url-display').textContent.trim());
  check(hook.startsWith(new URL(gw.url).origin + '/webhook/'), `webhook link not on this gateway: ${hook}`);
  await click(bob, '#webhook-created-close');
  await click(bob, '#integrations-btn');
  check(await until(bob, () => !!document.querySelector('#webhook-list-area [data-del-wh]')), 'webhook not listed');
  await click(bob, '#webhook-list-area [data-del-wh]');
  check(await until(bob, () => !document.querySelector('#webhook-list-area [data-del-wh]')),
    'deleted webhook still listed');
  await click(bob, '#ci-close-btn');

  step = 'delete-room';
  await click(bob, '#room-options-btn');
  await sleep(200);
  await click(bob, '#delete-room-btn');
  await confirmOk(bob);
  for (const [p, who] of [[alice, 'Alice'], [carol, 'Carol']]) {
    check(await until(p, (n) => ![...document.querySelectorAll('#room-list li')].some((l) => l.textContent.includes(n)), ROOM),
      `${who} still lists the deleted room`);
    check(await until(p, () => !document.getElementById('chat-header-name').textContent.includes('#')),
      `${who}'s header still names the deleted room`);
  }

  step = 'done';
  const errs = pages.flatMap((p) => p._errs);
  if (errs.length) fail('page errors:\n   - ' + errs.join('\n   - '));
  if (!process.exitCode) console.log(`  ✓ rooms OK: join, live members, leave, ban/unban, transfer, webhook, delete ("${ROOM}")`);
} catch (e) {
  fail(`threw: ${e.message}`);
} finally {
  await browser.close().catch(() => {});
  await gw.stop();
}
