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
import { skipSmoke } from './scripts/smoke-status.mjs';
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
if (!CHROME) skipSmoke('No Chrome/Edge found; set PROXION_CHROME.');

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

const promptOpen = () => { const m = document.getElementById('confirm-modal'); return !!m && getComputedStyle(m).display !== 'none'; };
const hasRoom = (id) => !!document.getElementById('nav-' + id);
const seesText = (txt) => Array.from(document.querySelectorAll('#message-feed .message')).some((el) => (el.textContent || '').includes(txt));
const unsupportedControlsHidden = () => ['schedule-btn', 'integrations-btn'].every((id) => {
  const el = document.getElementById(id);
  return !!el && getComputedStyle(el).display === 'none';
});
const enabledPoll = (question) => {
  const card = Array.from(document.querySelectorAll('.poll-card')).find((el) => el.querySelector('.poll-q')?.textContent === question);
  return !!card && card.querySelectorAll('.poll-opt').length === 2
    && Array.from(card.querySelectorAll('.poll-opt')).every((el) => !el.disabled && el.getAttribute('aria-disabled') === 'false');
};

async function waitFor(page, fn, arg, ms) {
  return page.waitForFunction(fn, { timeout: ms, polling: 500 }, arg).then(() => true).catch(() => false);
}
async function reload(page) {
  await page.reload({ waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(() => typeof window.proxionWebDm === 'object', { timeout: 30000 });
}
async function openRoom(page, id) {
  await page.waitForFunction(rid => !!document.getElementById('nav-' + rid), { timeout: 30000 }, id);
  await page.evaluate((rid) => document.getElementById('nav-' + rid).querySelector('.room-item-body').click(), id);
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
  if (!served) { await cleanup(); skipSmoke('could not build/serve over https (openssl?).'); }
  ok('built + serving the web app over https');

  step = 'css';
  css = await startCss({ allowSelfSignedClientId: true }).catch(() => null);
  if (!css) { await cleanup(); skipSmoke('no CSS pod available (offline / npx blocked).'); }
  const stamp = Date.now();
  const alice = await provisionAccount(css.url, { email: `alice-${stamp}@test.example`, password: 'pw-12345678', label: 'alice' });
  const bob = await provisionAccount(css.url, { email: `bob-${stamp}@test.example`, password: 'pw-12345678', label: 'bob' });
  const carol = await provisionAccount(css.url, { email: `carol-${stamp}@test.example`, password: 'pw-12345678', label: 'carol' });
  ok('CSS pod up + three accounts provisioned');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
  const pageA = await (await browser.createBrowserContext()).newPage();   // owner
  const pageB = await (await browser.createBrowserContext()).newPage();   // joiner
  const pageC = await (await browser.createBrowserContext()).newPage();   // joiner
  const errs = [];
  for (const [tag, p] of [['A', pageA], ['B', pageB], ['C', pageC]]) {
    p.on('pageerror', (e) => errs.push(`${tag}: ${e.message}`));
    if (process.env.PROXION_SMOKE_DEBUG) {
      p.on('console', (m) => { if (m.type() === 'warn' || m.type() === 'error') console.error(`[${tag}] ${m.type()}: ${m.text()}`); });
      p.on('response', (r) => { if (r.status() >= 400 && r.request().method() !== 'GET') console.error(`[${tag}] ${r.request().method()} ${r.status()} ${r.url()}`); });
    }
  }

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
  await pageA.waitForFunction(() => (document.getElementById('room-invite-url')?.textContent || '').includes('?join='), { timeout: 15000 }).catch(async (e) => {
    console.error('Create state:', await pageA.evaluate(() => ({ error: document.getElementById('room-create-error')?.textContent, busy: document.getElementById('room-create-submit')?.getAttribute('aria-busy') })));
    throw e;
  });
  const invite = await pageA.$eval('#room-invite-url', (el) => el.textContent.trim());
  const roomId = decodeURIComponent(new URL(invite).searchParams.get('join').split('~')[0]);
  if (new URL(invite).searchParams.get('mode') === 'web') ok('the invite link keeps mode=web');
  else fail(`invite has no mode=web: ${invite}`);
  // Reload as soon as the invite is exposed: creation must already be durable.

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
  if (await pageA.evaluate(unsupportedControlsHidden)) ok('unsupported scheduling and integrations controls stay hidden');
  else fail('browser room exposes scheduling or integrations');

  // ── Two join requests at once ──
  step = 'requests';
  await pageA.evaluate(async () => { window.proxionWebJoin.stop(); await window.proxionWebJoin.resume(); });
  const invitationTab = await pageB.browserContext().newPage();
  invitationTab.on('pageerror', error => errs.push('invite: ' + error.message));
  await invitationTab.goto(invite, { waitUntil: 'load' });
  await pageB.waitForFunction(code => document.getElementById('join-room-input')?.value === code
    && document.getElementById('join-room-modal')?.style.display === 'flex', { timeout: 20000, polling: 500 }, new URL(invite).searchParams.get('join')).catch(async error => {
      const snapshot = async () => ({ url: location.href, notice: document.getElementById('tab-notice')?.innerText,
        modal: document.getElementById('join-room-modal')?.style.display, input: document.getElementById('join-room-input')?.value,
        locks: await navigator.locks?.query(), dmReady: !!window.proxionWebDm });
      throw Error(error.message + JSON.stringify({ active: await pageB.evaluate(snapshot), invite: await invitationTab.evaluate(snapshot), errs }));
    });
  await invitationTab.waitForFunction(() => document.getElementById('tab-notice-body')?.textContent.includes('without signing in again'), { timeout: 15000, polling: 500 });
  if (await invitationTab.evaluate(() => !!window.proxionWebDm)) throw Error('Invite tab started a second active session');
  await invitationTab.close();
  await pageB.click('#join-room-submit-btn');
  const sentB = await waitFor(pageB, () => document.getElementById('join-pending-banner')?.classList.contains('visible'), null, 15000);
  ok('an invite in another tab opens in the existing signed-in tab without another login');
  const sentC = await requestJoin(pageC, invite);
  if (sentB && sentC) ok('both joiners sent a request (pending banner shown)');
  else fail(`request banners: bob=${sentB} carol=${sentC}`);

  step = 'pending-reload';
  await reload(pageC);
  if (await waitFor(pageC, () => document.getElementById('join-pending-banner')?.classList.contains('visible'), null, 15000)) ok('the pending banner survives the joiner\'s reload');
  else fail('the pending banner was gone after reload');

  step = 'prompts';
  const prompts = [];
  if (await waitFor(pageA, promptOpen, null, 40000)) {
    prompts.push(await pageA.$eval('#confirm-msg', (el) => el.textContent));
    await pageA.click('#confirm-ok');
  }
  await sleep(800);
  if (await waitFor(pageA, promptOpen, null, 40000)) {
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
  const cleared = await waitFor(declined, () => !document.getElementById('join-pending-banner')?.classList.contains('visible'), null, 40000);
  if (cleared) ok('the decline reached the declined joiner (banner cleared)');
  else fail('the declined joiner kept waiting');

  step = 'joined';
  if (await waitFor(joiner, hasRoom, roomId, 40000)) ok('the approved joiner got the room without a manual inbox refresh');
  else fail('the room never appeared for the approved joiner');

  step = 'joiner-reload';
  // Prove restoration comes from the pod, without this device's room cache.
  await joiner.evaluate(() => {
    for (const key of Object.keys(localStorage)) if (key.startsWith('proxion_joined_rooms:')) localStorage.removeItem(key);
  });
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

  step = 'pod-room-file';
  let rejectAsset = true;
  await joiner.setRequestInterception(true);
  joiner.on('request', request => {
    if (rejectAsset && request.method() === 'POST' && request.url() === `${alice.storageRoot}proxion/rooms/${roomId}/`) {
      rejectAsset = false; request.abort('failed').catch(() => {});
    } else request.continue().catch(() => {});
  });
  await joiner.evaluate(() => {
    const transfer = new DataTransfer(); transfer.items.add(new File(['shared room bytes'.repeat(2300)], 'shared.txt', { type: 'text/plain' }));
    const input = document.getElementById('file-input'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await joiner.waitForSelector('#file-confirm-modal', { visible: true }); await joiner.click('#file-confirm-ok');
  await joiner.waitForSelector('[data-room-media-retry]:not([disabled])', { timeout: 20000 });
  await reload(joiner); await openRoom(joiner, roomId);
  await joiner.waitForSelector('[data-room-media-retry]:not([disabled])', { timeout: 20000 });
  await joiner.click('[data-room-media-retry]');
  if (!(await waitFor(pageA, () => [...document.querySelectorAll('#message-feed [data-room-asset]')].some(button => button.parentElement.textContent.includes('shared.txt')), null, 30000))) throw Error('Shared attachment message did not arrive');
  await pageA.evaluate(() => [...document.querySelectorAll('#message-feed [data-room-asset]')].find(button => button.parentElement.textContent.includes('shared.txt')).click());
  if (!(await waitFor(pageA, () => document.querySelector('a[download="shared.txt"]')?.href === 'data:application/octet-stream;base64,' + btoa('shared room bytes'.repeat(2300)), null, 20000))) throw Error('Shared file bytes were not verified');
  await reload(pageA); await openRoom(pageA, roomId);
  await pageA.waitForSelector('[data-room-asset]', { timeout: 20000 });
  await pageA.evaluate(() => [...document.querySelectorAll('#message-feed [data-room-asset]')].find(button => button.parentElement.textContent.includes('shared.txt')).click());
  if (!(await waitFor(pageA, () => !!document.querySelector('a[download="shared.txt"]'), null, 20000))) throw Error('Attachment did not restore after reload');
  ok('member uploads a shared attachment; owner verifies exact bytes before and after reload');
  step = 'pod-room-voice';
  await joiner.click('#voice-record-btn');
  await joiner.waitForFunction(() => document.getElementById('voice-recording-bar').classList.contains('active'));
  await sleep(800); await joiner.click('#voice-record-btn');
  if (!(await waitFor(pageA, () => [...document.querySelectorAll('#message-feed [data-room-asset]')].some(button => button.parentElement.textContent.includes('voice.webm')), null, 30000))) throw Error('Pod voice note did not arrive');
  await pageA.evaluate(() => [...document.querySelectorAll('#message-feed [data-room-asset]')].find(button => button.parentElement.textContent.includes('voice.webm')).click());
  if (!(await waitFor(pageA, () => document.querySelector('.msg-audio-preview')?.readyState >= 1, null, 20000))) throw Error('Pod voice note did not decode');
  ok('member records a pod-room voice note; owner verifies and plays it');

  // Search the messages this device loaded, and state that scope explicitly.
  step = 'local-search';
  await pageA.type('#search-input', fromJoiner);
  const found = await waitFor(pageA, (text) => {
    const panel = document.getElementById('search-panel');
    return !!panel && !panel.hidden && Array.from(panel.querySelectorAll('.search-result')).some((el) => el.textContent.includes(text))
      && !!panel.querySelector('[data-search-scope]')?.textContent.includes('loaded on this device');
  }, fromJoiner, 10000);
  if (found) ok('browser search returns loaded messages with an explicit device scope');
  else fail('browser search did not return the loaded room message and scope note');
  await pageA.focus('#search-input');
  await pageA.keyboard.down('Control');
  await pageA.keyboard.press('a');
  await pageA.keyboard.up('Control');
  await pageA.keyboard.press('Backspace');
  await waitFor(pageA, () => document.getElementById('search-panel')?.hidden, null, 5000);

  step = 'poll-voting';
  const question = 'poll-' + Math.random().toString(36).slice(2, 7);
  if (!(await pageA.$eval('#poll-btn', el => el.getClientRects().length > 0))) await pageA.locator('label[for="compose-more-toggle"]').click();
  await pageA.locator('#poll-btn').click();
  await pageA.waitForSelector('#poll-modal', { visible: true });
  await pageA.type('#poll-question', question);
  await pageA.type('#poll-opt-1', 'A'); await pageA.type('#poll-opt-2', 'B');
  await pageA.click('#poll-submit');
  if (!(await waitFor(joiner, enabledPoll, question, 20000))) {
    const snapshot = () => ({ feed: document.getElementById('message-feed')?.innerText, draft: document.getElementById('message-input')?.value,
      modal: document.getElementById('poll-modal')?.style.display, toast: document.getElementById('toast')?.innerText });
    throw Error('Received poll is not votable: ' + JSON.stringify({ owner: await pageA.evaluate(snapshot), joiner: await joiner.evaluate(snapshot), errs }));
  }
  const clickVote = (args) => {
    const card = Array.from(document.querySelectorAll('.poll-card')).find(el => el.querySelector('.poll-q')?.textContent === args.question);
    card.querySelectorAll('.poll-opt')[args.option].click();
  };
  const countsMatch = ({ question, counts, own }) => {
    const card = Array.from(document.querySelectorAll('.poll-card')).find(el => el.querySelector('.poll-q')?.textContent === question);
    const options = Array.from(card?.querySelectorAll('.poll-opt') || []);
    return options.length === counts.length && options.every((el, i) => !el.disabled
      && Number(el.querySelector('.poll-opt-count').textContent || 0) === counts[i]
      && (el.getAttribute('aria-pressed') === 'true') === (i === own));
  };
  const check = async (page, counts, own) => {
    if (!(await waitFor(page, countsMatch, { question, counts, own }, 25000))) {
      const details = await page.evaluate(async ({ question, container, owner, roomId }) => {
        const card = Array.from(document.querySelectorAll('.poll-card')).find(el => el.querySelector('.poll-q')?.textContent === question);
        const api = await import('./pod.js'), auth = await import('./auth.js');
        const m = (await api.podReadChatDayAt(container, new Date(), roomId, owner)).find(m => m.content.startsWith('📊 ' + question));
        return { feed: document.getElementById('message-feed')?.innerText, questions: Array.from(document.querySelectorAll('.poll-q')).map(el => el.textContent), options: Array.from(card?.querySelectorAll('.poll-opt') || []).map(el => ({ count: el.querySelector('.poll-opt-count')?.textContent, pressed: el.getAttribute('aria-pressed'), disabled: el.disabled })),
          me: auth.solidSession.info.webId, localDid: localStorage.getItem('proxion_identity_did'),
          canonical: m && { content: m.content, reactions: m.reactions, revision: m.poll_revision, votes: Object.values(m.poll_vote_versions || {}).map(v => ({ actor: v.actor, revision: v.poll_revision, signer: v.proof.split('|')[0] })) },
          signer: (await api.podFetchPeerSigner(auth.podStorageRoot()))?.signer };
      }, { question, container: `${alice.storageRoot}proxion/rooms/${roomId}/`, owner: alice.webId, roomId });
      throw Error('Unexpected poll state: ' + JSON.stringify({ expected: { counts, own }, details, errs }));
    }
  };
  await joiner.evaluate(clickVote, { question, option: 0 });
  await check(joiner, [1,0], 0); await check(pageA, [1,0], null);
  await joiner.evaluate(clickVote, { question, option: 1 });
  await check(joiner, [0,1], 1); await check(pageA, [0,1], null);
  await joiner.evaluate(clickVote, { question, option: 1 });
  await check(joiner, [0,0], null); await check(pageA, [0,0], null);
  await joiner.evaluate(clickVote, { question, option: 0 }); await check(joiner, [1,0], 0);
  await pageA.evaluate(clickVote, { question, option: 0 });
  await check(pageA, [2,0], 0); await check(joiner, [2,0], 0);
  await joiner.reload({ waitUntil: 'load', timeout: 30000 });
  await openRoom(joiner, roomId); await check(joiner, [2,0], 0);
  ok('browser polls create, vote, switch, clear, merge voters, and survive reload');

  // Rebuild the composer for a different room, then return to the shared room.
  step = 'capabilities-after-room-switch';
  await pageA.evaluate(() => document.getElementById('create-room-btn').click());
  await pageA.waitForSelector('#room-name-input', { visible: true, timeout: 10000 });
  await pageA.type('#room-name-input', 'other-' + roomName);
  await pageA.click('#room-create-submit');
  await pageA.waitForFunction((old) => {
    const value = document.getElementById('room-invite-url')?.textContent || '';
    return value.includes('?join=') && value !== old;
  }, { timeout: 15000 }, invite);
  await pageA.evaluate(() => document.getElementById('room-create-done-btn')?.click());
  const otherInvite = await pageA.$eval('#room-invite-url', (el) => el.textContent.trim());
  const otherRoomId = decodeURIComponent(new URL(otherInvite).searchParams.get('join').split('~')[0]);
  await pageA.waitForFunction((id) => document.getElementById('nav-' + id)?.classList.contains('active'), { timeout: 10000 }, otherRoomId);
  if (!(await pageA.evaluate(unsupportedControlsHidden))) fail('unsupported controls reappeared in a newly created room');
  await openRoom(pageA, roomId);
  if (await pageA.evaluate(unsupportedControlsHidden) && await waitFor(pageA, enabledPoll, question, 10000)) {
    ok('room switching preserves poll voting and keeps unsupported controls hidden');
  } else fail('room switching broke poll capabilities');

  // ── Members list, then the joiner leaves ──
  step = 'members';
  await waitFor(pageA, () => !document.querySelector('.toast--error'), null, 5000);
  await pageA.evaluate(() => document.getElementById('members-toggle')?.click());
  if (await waitFor(pageA, () => document.querySelectorAll('#members-list .member-item').length >= 2, null, 15000)) ok('the owner\'s Members panel lists the owner and the joiner');
  else fail('the Members panel did not list the joiner');
  await sleep(300);
  if (await pageA.evaluate(() => !!document.querySelector('.toast--error'))) fail('opening Members showed an unrelated unsupported-action error');

  step = 'member-dm';
  const peerWebId = firstIsBob ? bob.webId : carol.webId;
  // A message's overflow menu pre-fills the author's address, not their name.
  await pageA.evaluate(text => {
    const row = Array.from(document.querySelectorAll('#message-feed .message')).find(el => el.textContent.includes(text));
    row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 300, clientY: 200 }));
  }, fromJoiner);
  await pageA.click('#ctx-add-friend');
  await pageA.waitForSelector('#add-peer-modal', { visible: true });
  if (await pageA.$eval('#add-peer-input', el => el.value) !== peerWebId) throw Error('Message Add friend did not prefill the author');
  await pageA.click('#add-peer-cancel-btn');

  // A regular member also has Add friend, with all moderation actions hidden.
  await joiner.evaluate(() => document.getElementById('members-toggle').click());
  await joiner.waitForFunction(webid => !!document.querySelector(`#members-list .member-item[data-webid="${CSS.escape(webid)}"]`), { timeout: 15000 }, alice.webId);
  await joiner.evaluate(webid => document.querySelector(`#members-list .member-item[data-webid="${CSS.escape(webid)}"]`)
    .dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 500, clientY: 200 })), alice.webId);
  if (await joiner.evaluate(() => Array.from(document.querySelectorAll('#member-context-menu [data-role-action]')).some(el => !el.hidden))) throw Error('Non-owner was offered moderation actions');
  await joiner.click('#member-context-menu [data-member-add-friend]');
  if (await joiner.$eval('#add-peer-input', el => el.value) !== alice.webId) throw Error('Member Add friend did not prefill the selected identity');
  await joiner.click('#add-peer-cancel-btn');

  // Click the member's name and add through the profile's action as well.
  await pageA.evaluate(() => { const panel = document.getElementById('members-panel'); panel.style.display = 'block'; });
  await pageA.evaluate(webid => document.querySelector(`#members-list .member-item[data-webid="${CSS.escape(webid)}"]`).click(), peerWebId);
  await pageA.waitForSelector('#profile-card.show', { timeout: 10000 });
  await pageA.click('#profile-add-friend-btn');
  await pageA.waitForSelector('#add-peer-modal', { visible: true });
  if (await pageA.$eval('#add-peer-input', el => el.value) !== peerWebId) throw Error('Profile Add friend did not prefill the selected identity');
  await pageA.click('#add-peer-submit-btn');
  await pageA.waitForSelector('#add-peer-modal', { hidden: true, timeout: 20000 });
  await waitFor(joiner, peer => window.proxionWebFriends?.stateFor(peer).state === 'incoming', alice.webId, 40000);
  await joiner.evaluate(peer => [...document.querySelectorAll('[data-bf-action="accept"]')].find(el => el.dataset.peer === peer).click(), alice.webId);
  if (!(await waitFor(pageA, peer => window.proxionWebFriends?.stateFor(peer).state === 'friends', peerWebId, 40000))) throw Error('Friend acceptance did not reach the requester');
  ok('Add friend works from messages and member names, including non-owner menus, and requires the selected person to accept');

  await openRoom(pageA, roomId);
  await pageA.evaluate(() => { document.getElementById('members-panel').style.display = 'block'; });
  await pageA.evaluate(webid => document.querySelector(`#members-list .member-item[data-webid="${CSS.escape(webid)}"]`).click(), peerWebId);
  if (!(await pageA.$eval('#profile-add-friend-btn', el => el.disabled && el.textContent === 'Friends'))) throw Error('Accepted friend still offers Add friend');
  await pageA.click('#profile-dm-btn');
  await waitFor(pageA, webid => !!document.getElementById('nav-' + webid), peerWebId, 10000);
  const dmText = 'member-dm-' + Date.now();
  await send(pageA, dmText);
  if (!(await waitFor(joiner, webid => !!document.getElementById('nav-' + webid), alice.webId, 40000))) throw Error('Member DM never arrived in recipient sidebar');
  await joiner.evaluate(webid => document.getElementById('nav-' + webid).click(), alice.webId);
  if (!(await waitFor(joiner, seesText, dmText, 15000))) throw Error('Member DM did not render');
  await send(joiner, dmText + '-reply');
  if (!(await waitFor(pageA, seesText, dmText + '-reply', 40000))) throw Error('Member DM reply did not arrive');
  await reload(pageA);
  if (!(await waitFor(pageA, webid => !!document.getElementById('nav-' + webid), peerWebId, 20000))) throw Error('Member contact was lost on reload');
  await pageA.evaluate(webid => document.getElementById('nav-' + webid).click(), peerWebId);
  if (!(await waitFor(pageA, seesText, dmText, 15000))) throw Error('Member DM history was lost on reload');
  ok('Send DM from a room member opens a real conversation, receives replies live and survives reload');
  await openRoom(pageA, roomId); await openRoom(joiner, roomId);

  step = 'existing-member-recovery';
  // Reproduce the reported split: the owner still lists this member, but the
  // member's own durable room registration is absent on a fresh load.
  await joiner.evaluate(async roomId => {
    const pod = await import('./pod.js');
    const records = await pod.podReadJoinedRooms();
    if (!(await pod.podWriteJoinedRooms(records.filter(rec => rec.roomId !== roomId)))) throw Error('Could not prepare missing room registration');
    for (const key of Object.keys(localStorage)) if (key.startsWith('proxion_joined_rooms:')) localStorage.removeItem(key);
  }, roomId);
  await reload(joiner);
  await sleep(1500);
  if (await joiner.evaluate(hasRoom, roomId)) throw Error('Missing-registration fixture still shows the room');
  // Auto-approval can settle before the transient pending banner is observed.
  await requestJoin(joiner, invite);
  if (!(await waitFor(joiner, hasRoom, roomId, 40000))) throw Error('Existing member room registration was not restored');
  if (await pageA.evaluate(promptOpen)) throw Error('Existing member recovery asked the owner to approve again');
  ok('an existing member with a missing room registration restores it from the invite without another approval');
  await openRoom(joiner, roomId);

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
