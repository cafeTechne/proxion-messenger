// Two-party call smoke (R82 V) — the real thing smoke_webrtc.mjs is NOT: two
// identities, two browsers, two federated gateways, a video call placed through the
// actual gateway signaling, media connecting peer to peer. Builds on the federation
// harness (add peer -> friend request -> accept -> DM), then Alice video-calls Bob,
// Bob answers and turns on his camera, and we assert BOTH sides receive a live remote
// video track and neither call was refused by the fingerprint check.
//
//   node web/smoke_call.mjs     (spawns both gateways; needs python + chrome)
//
// Exit 0 = bidirectional call media established; non-zero = a step failed.
// A real cross-network call through a TURN relay behind two NATs stays a manual check.

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
  const dataDir = mkdtempSync(join(tmpdir(), `proxion-call-${name}-`));
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

async function openClient(browser, url, label) {
  const page = await browser.newPage();
  page._console = [];
  page.on('console', m => page._console.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', e => { pageErrors.push(`${label}: ${e.message}`); page._console.push('PAGEERR: ' + e.message); console.error(`  [${label}] pageerror: ${e.message}`); });
  // Start as an onboarded user. Hiding the onboarding dialog is not enough:
  // the app reopens it on connect, and the page behind an open dialog is inert.
  await page.evaluateOnNewDocument((n) => {
    try { localStorage.setItem('proxion_display_name', n); localStorage.setItem('proxion_pod_setup_skipped', '1'); } catch { /* storage off */ }
  }, label);
  // Keep every toast text, since toasts expire before a slow check reads them.
  await page.evaluateOnNewDocument(() => {
    window.__toasts = [];
    new MutationObserver(() => {
      // A repeat of a visible toast only bumps its counter, so count that too.
      document.querySelectorAll('#toast-container .toast__msg').forEach((el) => {
        const n = el.parentElement?.querySelector('.toast__count')?.textContent || '';
        if (el.__seen !== n) { el.__seen = n; window.__toasts.push(el.textContent); }
      });
    }).observe(document, { childList: true, subtree: true, characterData: true });
  });
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await page.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 });
  return page;
}

// A remote video track present in the call widget's remote <video> = media arrived.
const remoteVideoTracks = (page) => page.evaluate(() => {
  const v = document.getElementById('vw-remote-video');
  const s = v && v.srcObject;
  return s && s.getVideoTracks ? s.getVideoTracks().length : 0;
});

let step = 'init';
// An uncaught exception in any page fails the run, even if every step passed.
const pageErrors = [];
let browser = null;
const fail = (m) => { console.error(`  ✗ [${step}] ${m}`); process.exitCode = 1; };
try {
  step = 'spawn-gateways';
  const [A, B] = [await startGateway('alice'), await startGateway('bob')];

  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: [
      '--ignore-certificate-errors', '--no-sandbox', '--disable-gpu',
      '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
      '--autoplay-policy=no-user-gesture-required',
    ],
  });

  step = 'connect-both';
  const alice = await openClient(browser, A.url, 'alice');
  const bob = await openClient(browser, B.url, 'bob');

  step = 'befriend';
  await bob.evaluate(() => document.getElementById('settings-btn')?.click());
  const bobAddr = await bob.waitForFunction(
    () => window.proxionAddress || localStorage.getItem('proxion_my_address'),
    { timeout: 10000 }).then(h => h.jsonValue()).catch(() => null);
  await bob.evaluate(() => { const m = document.getElementById('settings-modal'); if (m) m.style.display = 'none'; });
  if (!bobAddr || !bobAddr.includes('@')) { fail(`could not read Bob's address (${bobAddr})`); throw new Error('stop'); }
  await alice.evaluate(() => document.getElementById('add-peer-btn').click());
  await alice.waitForFunction(() => { const m = document.getElementById('add-peer-modal'); return m && getComputedStyle(m).display !== 'none'; }, { timeout: 5000 });
  await alice.type('#add-peer-input', bobAddr);
  await alice.evaluate(() => document.getElementById('add-peer-submit-btn').click());
  await bob.waitForFunction(() => document.querySelector('#friend-request-list li [data-fr-action="accept"]'), { timeout: 20000 })
    .catch(() => fail('friend request never arrived at Bob'));
  if (process.exitCode) throw new Error('stop');
  await bob.evaluate(() => document.querySelector('#friend-request-list li [data-fr-action="accept"]').click());

  step = 'open-dms';
  for (const [pg, who] of [[bob, 'bob'], [alice, 'alice']]) {
    await pg.waitForFunction(() => document.querySelector('#dm-list li.dm-item'), { timeout: 20000 })
      .catch(() => fail(`contact never appeared for ${who}`));
    if (process.exitCode) throw new Error('stop');
    await sleep(400);
    await pg.evaluate(() => document.querySelector('#dm-list li.dm-item').click());
    await pg.waitForFunction(() => { const h = document.getElementById('chat-header-name'); return h && !/welcome/i.test(h.textContent || ''); }, { timeout: 8000 })
      .catch(() => fail(`${who} could not open the DM thread`));
    if (process.exitCode) throw new Error('stop');
  }

  step = 'alice-video-call';
  // Alice starts a video call; the pre-join preview opens, then Join.
  await alice.waitForFunction(() => { const b = document.getElementById('start-video-call-btn'); return b && b.offsetParent !== null; }, { timeout: 8000 })
    .catch(async () => {
      const diag = await alice.evaluate(() => {
        const cb = document.getElementById('start-call-btn');
        const vb = document.getElementById('start-video-call-btn');
        return {
          type: (window.activeView && window.activeView.type) || '(unknown)',
          callBtn: cb ? getComputedStyle(cb).display + '/off=' + (cb.offsetParent !== null) : 'missing',
          videoBtn: vb ? getComputedStyle(vb).display + '/off=' + (vb.offsetParent !== null) : 'missing',
        };
      });
      fail(`video-call button not visible. diag=${JSON.stringify(diag)}`);
    });
  if (process.exitCode) throw new Error('stop');
  await alice.evaluate(() => document.getElementById('start-video-call-btn').click());
  await alice.waitForFunction(() => { const d = document.getElementById('call-preview'); return d && getComputedStyle(d).display !== 'none'; }, { timeout: 8000 })
    .catch(() => fail('call preview did not open'));
  if (process.exitCode) throw new Error('stop');
  await sleep(700);   // let the preview acquire the fake camera

  // R82 Y: verify the pre-join preview's interactive bits actually work.
  step = 'preview-checks';
  const pv = await alice.evaluate(() => {
    const v = document.getElementById('preview-video');
    const s = v && v.srcObject;
    return {
      hasVideo: !!(s && s.getVideoTracks && s.getVideoTracks().length),
      camSel: !!document.getElementById('preview-camera'),
      micSel: !!document.getElementById('preview-mic'),
    };
  });
  if (!pv.hasVideo) fail('preview camera did not render (srcObject has no video track)');
  if (!pv.camSel || !pv.micSel) fail('preview device pickers are missing');
  if (process.exitCode) throw new Error('stop');

  await alice.evaluate(() => document.getElementById('preview-join').click());

  step = 'bob-answer';
  await bob.waitForFunction(() => { const b = document.getElementById('voice-banner'); return b && getComputedStyle(b).display !== 'none'; }, { timeout: 20000 })
    .catch(() => fail('incoming call never rang on Bob (cross-gateway voice signaling)'));
  if (process.exitCode) throw new Error('stop');
  await bob.evaluate(() => document.getElementById('voice-answer').click());
  await sleep(800);
  await bob.evaluate(() => document.getElementById('camera-btn')?.click());   // Bob adds video

  step = 'assert-media';
  // Both sides must receive a live remote VIDEO track within the ICE/negotiation window.
  const ok = await Promise.all([
    bob.waitForFunction(() => { const v = document.getElementById('vw-remote-video'); return v && v.srcObject && v.srcObject.getVideoTracks && v.srcObject.getVideoTracks().length > 0; }, { timeout: 20000 }).then(() => true).catch(() => false),
    alice.waitForFunction(() => { const v = document.getElementById('vw-remote-video'); return v && v.srcObject && v.srcObject.getVideoTracks && v.srcObject.getVideoTracks().length > 0; }, { timeout: 20000 }).then(() => true).catch(() => false),
  ]);
  const notRefused = await Promise.all([
    bob.evaluate(() => !/could not verify/i.test(document.body.textContent || '')),
    alice.evaluate(() => !/could not verify/i.test(document.body.textContent || '')),
  ]);
  console.log(`  · remote video: Bob<-Alice=${await remoteVideoTracks(bob)}  Alice<-Bob=${await remoteVideoTracks(alice)}`);
  if (!ok[0] || !ok[1] || !notRefused[0] || !notRefused[1]) {
    for (const [pg, who] of [[alice, 'alice'], [bob, 'bob']]) {
      const st = await pg.evaluate(() => {
        const rv = document.getElementById('vw-remote-video');
        const s = rv && rv.srcObject;
        return {
          remoteV: s && s.getVideoTracks ? s.getVideoTracks().length : -1,
          remoteA: s && s.getAudioTracks ? s.getAudioTracks().length : -1,
          widget: getComputedStyle(document.getElementById('voice-widget') || document.body).display,
        };
      });
      console.error(`  · ${who} el=${JSON.stringify(st)}`);
      const cv = pg._console.filter(l => /error|warn/i.test(l)).slice(-10);
      if (cv.length) console.error(`  · ${who} console:\n${cv.map(l => '    ' + l).join('\n')}`);
    }
  }
  if (!ok[0]) fail('Bob never received Alice\'s video');
  if (!ok[1]) fail('Alice never received Bob\'s video');
  if (!notRefused[0] || !notRefused[1]) fail('a call was refused by the fingerprint check (identity unverified)');

  // R85 Track 1: cross-gateway identity verification is bound to the contact roster,
  // so a legitimate call must read Verified on BOTH sides (not merely not-refused).
  step = 'assert-verified';
  const verified = await Promise.all([
    bob.evaluate(() => document.getElementById('vw-verified')?.dataset.state || 'none'),
    alice.evaluate(() => document.getElementById('vw-verified')?.dataset.state || 'none'),
  ]);
  console.log(`  · verify state: Bob=${verified[0]}  Alice=${verified[1]}`);
  if (verified[0] !== 'verified') fail(`Bob's call did not verify Alice's identity cross-gateway (state=${verified[0]})`);
  if (verified[1] !== 'verified') fail(`Alice's call did not verify Bob's identity cross-gateway (state=${verified[1]})`);

  // R82 Y: the in-call quality selector changes and persists.
  step = 'quality-check';
  const q = await alice.evaluate(() => {
    const s = document.getElementById('vw-quality');
    if (!s) return null;
    s.value = 'saver';
    s.dispatchEvent(new Event('change'));
    try { return localStorage.getItem('proxion_call_quality'); } catch { return null; }
  });
  if (q !== 'saver') fail(`quality selector did not persist (got ${q})`);

  // Call lifecycle across gateways: every way a call ends must reach the other
  // side promptly, never leave it ringing or sitting in a dead call.
  const shown = (pg, id) => pg.evaluate((i) => { const e = document.getElementById(i); return !!e && getComputedStyle(e).display !== 'none'; }, id);
  // Interval polling: the default rAF polling never fires in a background tab.
  const until = (pg, fn, arg, ms = 8000) => pg.waitForFunction(fn, { timeout: ms, polling: 200 }, arg).then(() => true).catch(() => false);
  const hidden = (pg, id, ms) => until(pg, (i) => { const e = document.getElementById(i); return !e || getComputedStyle(e).display === 'none'; }, id, ms);
  const ringing = (pg, ms) => until(pg, () => { const b = document.getElementById('voice-banner'); return b && getComputedStyle(b).display !== 'none'; }, null, ms);
  const toasted = (pg, re, ms = 5000) => until(pg, (src) => (window.__toasts || []).some((x) => new RegExp(src, 'i').test(x)), re, ms);
  const click = (pg, id) => pg.evaluate((i) => document.getElementById(i).click(), id);

  const pressed = (pg, id) => pg.evaluate((i) => document.getElementById(i)?.getAttribute('aria-pressed'), id);
  // Back to a fresh page with the DM open, as after a reload.
  const reopen = async (pg, url, who) => {
    await pg.goto(url, { waitUntil: 'load', timeout: 20000 });
    await pg.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
    const ok = await until(pg, () => document.querySelector('.dot')?.classList.contains('online')
      && document.querySelector('#dm-list li.dm-item'), null, 20000);
    if (!ok) { fail(`${who} did not come back online with the contact`); throw new Error('stop'); }
    await pg.evaluate(() => document.querySelector('#dm-list li.dm-item').click());
    if (!(await until(pg, () => { const b = document.getElementById('start-call-btn'); return b && b.offsetParent !== null; }, null, 8000))) {
      fail(`${who} could not reopen the DM`); throw new Error('stop');
    }
  };

  step = 'in-call-controls';
  await click(alice, 'mute-btn');
  if ((await pressed(alice, 'mute-btn')) !== 'true') fail('mute button did not mute');
  await click(alice, 'mute-btn');
  if ((await pressed(alice, 'mute-btn')) !== 'false') fail('mute button did not unmute');
  await click(bob, 'camera-btn');   // Bob's camera was on: turn it off
  if (!(await until(bob, () => document.getElementById('camera-btn')?.getAttribute('aria-pressed') === 'false', null, 3000))) fail('camera button did not turn the camera off');
  await click(alice, 'screenshare-btn');
  if (!(await until(alice, () => document.getElementById('screenshare-btn')?.getAttribute('aria-pressed') === 'true', null, 5000))) fail('screen share did not start');
  else {
    if (!(await until(bob, () => { const v = document.getElementById('vw-remote-video'); return v && getComputedStyle(v).display !== 'none'; }, null, 10000))) fail('Bob never saw Alice\'s shared screen');
    await click(alice, 'screenshare-btn');
    if (!(await until(alice, () => document.getElementById('screenshare-btn')?.getAttribute('aria-pressed') === 'false', null, 3000))) fail('screen share did not stop');
  }

  step = 'callee-hangs-up';
  await click(bob, 'end-call');
  if (!(await hidden(alice, 'voice-widget'))) fail('Bob hung up but Alice stayed in the call');
  if (!(await toasted(alice, 'call ended'))) fail('Alice was not told the call ended');

  step = 'redial-after-call';
  // The call connected, so the redial is not throttled.
  await click(alice, 'start-call-btn');
  if (!(await ringing(bob, 15000))) fail('immediate redial after a connected call never rang');
  await click(bob, 'voice-answer');
  if (!(await until(alice, () => /\d\d:\d\d/.test(document.getElementById('vw-status')?.textContent || ''), null, 15000))) fail('redialed call never connected');

  step = 'peer-reloads';
  await alice.evaluate(() => { window.__toasts = []; });
  await bob.reload({ waitUntil: 'load' });
  if (!(await hidden(alice, 'voice-widget'))) fail('Bob reloaded mid-call but Alice stayed in the call');
  if (!(await toasted(alice, 'call ended'))) fail(`Alice was not told the call ended when Bob reloaded (${await alice.evaluate(() => window.__toasts.join(' | '))})`);
  await reopen(bob, B.url, 'bob');

  step = 'peer-offline';
  await bob.goto('about:blank');
  await sleep(1000);
  await click(alice, 'start-call-btn');
  if (!(await toasted(alice, 'unavailable', 8000))) fail('calling an offline contact gave no "unavailable" message');
  if (!(await hidden(alice, 'voice-widget', 3000))) fail('calling an offline contact left Alice stuck in "Calling..."');
  await reopen(bob, B.url, 'bob');

  step = 'caller-cancels';
  // Not throttled: the offline attempt rang nobody.
  await click(alice, 'start-call-btn');
  if (!(await ringing(bob, 15000))) fail('call after the contact came back online never rang');
  await click(alice, 'end-call');
  if (!(await hidden(bob, 'voice-banner'))) fail('Alice cancelled but Bob kept ringing');
  if (!(await toasted(bob, 'missed call'))) fail('Bob got no missed-call notice for the cancelled ring');

  step = 'redial-after-cancel';
  // A cancelled ring can be redialed: the pair gets a few rings a minute.
  for (const n of [2, 3]) {
    await click(alice, 'start-call-btn');
    if (!(await ringing(bob, 15000))) fail(`redial ${n} after a cancelled ring never rang`);
    await click(alice, 'end-call');
    if (!(await hidden(bob, 'voice-banner'))) fail(`Alice cancelled redial ${n} but Bob kept ringing`);
  }

  step = 'throttled-redial';
  // A fourth unanswered ring in a minute IS throttled: the error must end
  // "Calling...", not hang.
  await click(alice, 'start-call-btn');
  if (!(await toasted(alice, 'wait a moment'))) fail(`throttled redial gave no message (${await alice.evaluate(() => window.__toasts.join(' | '))})`);
  if (!(await hidden(alice, 'voice-widget', 3000))) fail('throttled redial left Alice stuck in "Calling..."');

  step = 'callee-declines';
  await click(bob, 'start-call-btn');
  if (!(await ringing(alice, 15000))) fail('Bob\'s call never rang on Alice');
  await click(alice, 'voice-decline');
  if (!(await hidden(bob, 'voice-widget'))) fail('Alice declined but Bob kept ringing out');
  if (!(await toasted(bob, 'declined'))) fail('Bob was not told the call was declined');
  if (await shown(alice, 'voice-banner')) fail('Alice\'s banner stayed up after declining');

  step = 'done';
  if (pageErrors.length) fail(`uncaught page errors: ${pageErrors.join(' | ')}`);
  if (!process.exitCode) {
    console.log('  ✓ two-party call OK — cross-gateway signaling, both answered, bidirectional');
    console.log('    video media, and the preview + quality controls work interactively.');
  }
} catch (e) {
  if (e.message !== 'stop') console.error(`  ✗ [${step}] threw: ${e.message}`);
  if (!process.exitCode) process.exitCode = 1;
  if (process.exitCode) {
    try { console.error('  · alice console tail:\n' + (global.__a?._console?.slice(-8).map(l => '    ' + l).join('\n') || '')); } catch {}
  }
} finally {
  if (browser) await browser.close();
  for (const p of procs) { try { p.kill(); } catch {} }
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch {} }
}
