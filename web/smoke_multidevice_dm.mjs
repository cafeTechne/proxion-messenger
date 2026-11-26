import { nativeFeedbackJourney } from './scripts/native-feedback-journey.mjs';
import { dmOutboxJourney } from './scripts/dm-outbox-journey.mjs';
import { nativeNavigationJourney } from './scripts/native-navigation-journey.mjs';
import { nativeActionJourney } from './scripts/native-action-journey.mjs';
import { nativeReactionJourney } from './scripts/native-reaction-journey.mjs';
import { nativeReplyJourney } from './scripts/native-reply-journey.mjs';
import { nativeCaptionJourney } from './scripts/native-caption-journey.mjs';
import { nativeMediaJourney } from './scripts/native-media-journey.mjs';
import { nativeChunkJourney } from './scripts/native-chunk-journey.mjs';
import { nativeInlineAdaptationJourney } from './scripts/native-inline-adaptation-journey.mjs';
import { nativeDeclineJourney } from './scripts/native-decline-journey.mjs';
import { nativeGifJourney, nativeBlockJourney, auditNativeVoiceHistory } from './scripts/native-policy-route-journey.mjs';
// Cross-account, multi-device DM smoke (R52 E6) — the "never green-verified"
// case from memory/multidevice-status.md.
//
// Topology: gateway A hosts account A on TWO paired devices (A-primary +
// A-device, linked via the delegation pairing flow); gateway B hosts peer B.
// B and A friend across gateways, then B sends A a DM. Assertions:
//   1. Pairing links A-device to account A (delegation cert for A's account).
//   2. The cross-gateway E2E DM from B reaches AND decrypts on A-primary.
//   3. R53 CROSS-GATEWAY DEVICE FANOUT: the same DM ALSO decrypts on A-device —
//      B's gateway fetched A's device roster (signed POST /devices) and relayed
//      a device-addressed E2E envelope; no "[could not decrypt]" anywhere.
//   4. A-primary's REPLY reaches B cross-gateway (regression guards for the
//      sealed-relay seal-key clobber AND the discarded-fanout-encrypt ratchet
//      poisoning — both used to silently kill reply-after-receiving).
//   5. SENDER SELF-SYNC (E5 slice 2): a message A-primary SENDS also appears on
//      A-device (same-gateway self-fanout, so it works even though B is remote).
//   6. After reloading, the DM is still readable plaintext on A-primary (dmhistory).
//
//   node web/smoke_multidevice_dm.mjs     (spawns two gateways; needs python + chrome)
//
// Exit 0 = the four assertions above held. A non-zero exit with a specific step
// tells us exactly which layer (pairing / federation / degradation / history) broke.
import { existsSync, mkdtempSync, rmSync } from 'fs';
import { spawn, execFileSync } from 'child_process';
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
const MSG = 'mddm-' + Math.random().toString(36).slice(2, 7);   // B -> A
const REPLY = 'mdrep-' + Math.random().toString(36).slice(2, 7); // A-primary -> B (self-syncs to A-device)

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
  let p;
  const launch = async () => {
    p = spawn('python', ['scripts/run_test_gateway.py'], { cwd: REPO, env });
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
  };
  await launch();
  return { url: `http://127.0.0.1:${httpPort}/`, httpPort, wsPort,
    nativeState: () => JSON.parse(execFileSync('python', ['-c',
      'import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); print(json.dumps(c.execute("SELECT message_id,status,header_committed,event_json IS NOT NULL,incoming FROM native_media").fetchall()))',
      join(dataDir, 'proxion.db')], { encoding: 'utf8' })),
    nativeParts: message => JSON.parse(execFileSync('python', ['-c',
      'import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); c.row_factory=sqlite3.Row; r=c.execute("SELECT n.queue_id,n.envelope_digest,n.header_digest,n.cipher_digest,n.transport,n.status,n.header_committed,n.chunk_count,n.custody_complete,COUNT(p.seq) AS parts FROM native_media n LEFT JOIN native_media_parts p ON p.queue_id=n.queue_id WHERE n.message_id=? GROUP BY n.queue_id",(sys.argv[2],)).fetchone(); print(json.dumps(dict(r) if r else {}))',
      join(dataDir, 'proxion.db'), message], { encoding: 'utf8' })),
    queued: (owner, device) => Number(execFileSync('python', ['-c',
      'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute("SELECT COUNT(*) FROM pending_device_fanout WHERE to_webid=? AND to_device_id=?",sys.argv[2:]).fetchone()[0])',
      join(dataDir, 'proxion.db'), owner, device], { encoding: 'utf8' }).trim()),
    pause: async () => {
      if (p.exitCode !== null) return;
      const exited = new Promise(resolve => p.once('exit', resolve)); p.kill('SIGKILL'); await exited;
    },
    resume: launch,
    restart: async () => {
      const exited = new Promise(resolve => p.once('exit', resolve));
      p.kill('SIGKILL'); await exited; await launch();
    },
  };
}

const online = (page) => page.waitForFunction(
  () => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 });

async function openClient(ctx, url, label) {
  const page = await ctx.newPage();
  page._console = [];
  page.on('console', m => page._console.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', e => { pageErrors.push(`${label}: ${e.message}`); page._console.push('PAGEERR: ' + e.message); console.error(`  [${label}] pageerror: ${e.message}`); });
  // Start as an onboarded user. Hiding the onboarding dialog is not enough:
  // the app reopens it on connect, and the page behind an open dialog is inert.
  await page.evaluateOnNewDocument((n) => {
    try { localStorage.setItem('proxion_display_name', n); localStorage.setItem('proxion_pod_setup_skipped', '1'); } catch { /* storage off */ }
  }, label);
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await online(page);
  return page;
}

const feedHas = (page, t) => page.evaluate((x) => (document.getElementById('message-feed')?.textContent || '').includes(x), t);
async function openFirstDm(page, who) {
  await page.waitForFunction(() => document.querySelector('#dm-list li.dm-item'), { timeout: 20000 });
  await sleep(400);
  await page.evaluate(() => (document.querySelector('#dm-list li.dm-item')).click());
  await page.waitForFunction(() => {
    const h = document.getElementById('chat-header-name'); return h && !/welcome/i.test(h.textContent || '');
  }, { timeout: 8000 });
}

let step = 'init';
// An uncaught exception in any page fails the run, even if every step passed.
const pageErrors = [];
let browser = null;
const fail = (m) => { console.error(`  ✗ [${step}] ${m}`); process.exitCode = 1; };
try {
  step = 'spawn-gateways';
  const [A, B] = [await startGateway('acct-a'), await startGateway('acct-b')];
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
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
  step = 'inject-fanout-history-failure';
  await ad.evaluate(() => {
    const put = IDBObjectStore.prototype.put;
    window.__fanoutHistoryFailures = 0;
    IDBObjectStore.prototype.put = function (...args) {
      if (this.name === 'messages') {
        window.__fanoutHistoryFailures++;
        throw new DOMException('fanout history interruption', 'QuotaExceededError');
      }
      return put.apply(this, args);
    };
  });
  step = 'b-send-dm';
  // Give B's client time to complete the async cross-gateway device-roster
  // fetch (get_peer_device_keys -> gw B -> POST /devices on gw A) that DM-open
  // triggered — fanout needs the roster before the first send.
  await sleep(1800);
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

  step = 'a-device-receive';   // R53: cross-gateway per-device fanout — A's
  // SECOND device must also receive AND decrypt B's DM (B's gateway fetched A's
  // device roster via POST /devices and relayed a device-addressed envelope).
  await ad.waitForFunction((t) => document.getElementById('message-feed')?.textContent.includes(t), { timeout: 15000 }, MSG)
    .catch(async () => {
      const feed = await ad.evaluate(() => document.getElementById('message-feed')?.textContent || '');
      fail(/could not decrypt|decryption error/i.test(feed)
        ? `B's DM reached A-device but failed to DECRYPT (wrong per-device key?)`
        : `B's DM did NOT fan out to A-device cross-gateway (roster fetch or envelope relay)`);
      console.error(`  · A-device console tail:\n${ad._console.slice(-8).map(l => '    ' + l).join('\n')}`);
      const gwB2 = (procs[1]._log() || '').split('\n').filter(l => /devices|fanout|relay|400|403/i.test(l)).slice(-8);
      console.error(`  · gateway-B log:\n${gwB2.map(l => '      ' + l).join('\n') || '      (none)'}`);
    });
  if (process.exitCode) throw new Error('stop');
  // And no decrypt-failure garbage anywhere on A-device.
  const adFeed = await ad.evaluate(() => document.getElementById('message-feed')?.textContent || '');
  if (/could not decrypt|decryption error/i.test(adFeed)) {
    fail('A-device rendered "[could not decrypt]" garbage');
  }
  if (process.exitCode) throw new Error('stop');

  step = 'recover-device-fanout';
  const recovery = await ad.evaluate(async text => {
    const { pendingDmReceives } = await import('./e2e.js');
    return { failures: window.__fanoutHistoryFailures,
      pending: (await pendingDmReceives()).filter(row => row.content === text).length };
  }, MSG);
  if (!recovery.failures || recovery.pending !== 1) throw Error('fanout encrypted recovery missing: ' + JSON.stringify(recovery));
  if (A.queued(apDid, adDid) !== 1) throw Error('socket write removed fanout before durable client commit');
  await A.restart(); await online(ap);
  if (A.queued(apDid, adDid) !== 1) throw Error('gateway restart lost uncommitted live delivery');
  const commitFault = await ad.evaluateOnNewDocument(() => {
    window.__holdDmCommit = true; window.__lostDmCommits = 0;
    const send = WebSocket.prototype.send;
    WebSocket.prototype.send = function (wire) {
      let frame; try { frame = JSON.parse(wire); } catch { /* other frame */ }
      if (frame?.cmd === 'dm_receive_commit' && window.__holdDmCommit) { window.__lostDmCommits++; return; }
      return send.call(this, wire);
    };
  });
  await ad.reload({ waitUntil: 'load', timeout: 20000 });
  await ad.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await online(ad);
  await openFirstDm(ad, 'A-device recovery');
  await ad.waitForFunction(text => document.getElementById('message-feed')?.textContent.includes(text), { timeout: 15000 }, MSG);
  const remaining = await ad.evaluate(async text => {
    const { pendingDmReceives } = await import('./e2e.js');
    return (await pendingDmReceives()).filter(row => row.content === text).length;
  }, MSG);
  if (remaining) throw Error('fanout recovery was not cleaned after reload');
  if (A.queued(apDid, adDid) !== 1 || !await ad.evaluate(() => window.__lostDmCommits)) throw Error('lost commit did not retain gateway replay');
  await ad.reload({ waitUntil: 'load', timeout: 20000 }); await online(ad);
  await openFirstDm(ad, 'committed receipt replay');
  await ad.evaluate(() => { window.__holdDmCommit = false; });
  await ad.removeScriptToEvaluateOnNewDocument(commitFault.identifier);
  for (let attempt = 0; attempt < 100 && A.queued(apDid, adDid) !== 0; attempt++) await sleep(100);
  if (A.queued(apDid, adDid) !== 0) throw Error('receipt replay did not acknowledge after lost commit');
  const recoveredCount = await ad.evaluate(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).filter(row => row.content === text).length, MSG);
  if (recoveredCount !== 1) throw Error('lost commit replay duplicated or lost readable history');
  console.log('  ✓ device fanout history failure survives gateway SIGKILL and recipient reload until durable commit');

  // ── E5 slice 2: A-primary SENDS a DM to B; it must self-sync to A-device ──
  // (Self-sync targets our own account on our own gateway, so it works even
  // though B — the peer — is single-device on another gateway.)
  step = 'a-primary-send-reply';
  await ap.focus('#message-input');
  await ap.type('#message-input', REPLY);
  await ap.keyboard.press('Enter');

  // The reply must reach B cross-gateway (regression guard for the sealed-relay
  // seal-key clobber: receiving a DM used to overwrite the peer's gateway seal
  // key with their browser key, so every reply sealed to the wrong key and was
  // dropped with a 400 the sender never saw).
  step = 'b-receives-reply';
  await b.waitForFunction((t) => document.getElementById('message-feed')?.textContent.includes(t), { timeout: 15000 }, REPLY)
    .catch(async () => {
      fail(`A-primary's cross-gateway reply "${REPLY}" never reached B (reply-after-receive relay delivery)`);
      const gwA2 = (procs[0]._log() || '').split('\n').filter(l => /relay|devices|Relayed|400|403/i.test(l)).slice(-8);
      console.error(`  · gateway-A relay log:\n${gwA2.map(l => '      ' + l).join('\n') || '      (none)'}`);
      const bFeed = await b.evaluate(() => (document.getElementById('message-feed')?.textContent || '').slice(-140));
      console.error(`  · B feed tail: "${bFeed}"`);
    });
  if (process.exitCode) throw new Error('stop');

  // Exercise the real native sealed chunk path, including receiver history.
  step = 'native-chunk-history';
  const nativeName = 'native-history-' + Date.now() + '.txt';
  const nativeHash = await ap.evaluate(async name => {
    const bytes = new Uint8Array(600 * 1024);
    for (let index = 0; index < bytes.length; index++) bytes[index] = 65 + index % 26;
    const data = new DataTransfer(); data.items.add(new File([bytes], name, { type: 'text/plain' }));
    const input = document.getElementById('file-input'); input.files = data.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
  }, nativeName);
  await ap.waitForSelector('#file-confirm-modal', { visible: true, timeout: 10000 });
  await ap.click('#file-confirm-ok');
  await b.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).length === 1,
    { timeout: 30000, polling: 250 }, nativeName);
  await b.reload({ waitUntil: 'load' }); await online(b); await openFirstDm(b, 'native file history');
  const restoredFile = await b.evaluate(async name => {
    const history = await import('./dmhistory.js'), rows = await history.dmHistorySearch(name);
    const record = rows[0] && await history.dmHistoryGet(rows[0].message_id);
    const bytes = Uint8Array.from(atob(record?.file?.data_b64 || ''), c => c.charCodeAt(0));
    return { count: rows.length, bytes: bytes.length,
      hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('') };
  }, nativeName);
  if (restoredFile.count !== 1 || restoredFile.bytes !== 600 * 1024 || restoredFile.hash !== nativeHash) throw Error('native file history lost/corrupted bytes: ' + JSON.stringify(restoredFile));
  console.log('  ✓ native sealed chunk file commits exact bytes before receipt and survives receiver reload');

  step = 'native-voice-history';
  await ap.bringToFront();
  const mic = await ap.$('#voice-record-btn'), micBox = await mic.boundingBox();
  if (!micBox) throw Error('native voice control unavailable');
  await ap.mouse.move(micBox.x + micBox.width / 2, micBox.y + micBox.height / 2);
  await ap.mouse.down(); await sleep(1500); await ap.mouse.up();
  await b.waitForFunction(async () => (await (await import('./dmhistory.js')).dmHistorySearch('voice.webm')).some(row => row.file?.mime_type === 'audio/webm'),
    { timeout: 20000, polling: 250 });
  console.log('  ✓ native DM voice records and sends through the sealed attachment route');
  if (process.env.PROXION_NATIVE_FEEDBACK_ONLY) {
    step = 'native-live-feedback';
    await nativeFeedbackJourney({ sender: ap, receiver: b, sourceGateway: A,
      openSender: () => openFirstDm(ap, 'feedback sender'), openReceiver: () => openFirstDm(b, 'feedback recipient'), online });
  } else if (process.env.PROXION_NATIVE_NAV_ONLY) {
    step = 'native-reference-navigation';
    await nativeNavigationJourney({ sender: ap, receiver: b, sourceGateway: A, openSender: () => openFirstDm(ap, 'navigation sender'), online });
  } else if (process.env.PROXION_NATIVE_ACTION_ONLY) {
    step = 'native-action-fixtures';
    await b.bringToFront();
    await b.evaluate(() => {
      const transfer = new DataTransfer(); transfer.items.add(new File(['B action original'], 'native-caption-action-fixture.txt', { type: 'text/plain' }));
      const input = document.getElementById('file-input'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await b.waitForSelector('#file-confirm-modal', { visible: true }); await b.click('#file-confirm-ok');
    await ap.bringToFront(); await ap.waitForFunction(async () => (await (await import('./dmhistory.js')).dmHistorySearch('native-caption-action-fixture')).some(row => row.file), { timeout: 30000 });
    await b.bringToFront(); await b.waitForFunction(async () => (await (await import('./dmhistory.js')).dmHistorySearch('native-caption-action-fixture')).some(row => row.file), { timeout: 30000 });
    await ap.bringToFront(); await ap.focus('#message-input'); await ap.type('#message-input', 'text-after-native-reaction-fixture-' + Date.now()); await ap.keyboard.press('Enter');
    step = 'native-delete-pin-references';
    await nativeActionJourney({ sender: ap, receiver: b, linked: ad, sourceGateway: A, targetGateway: B,
      openSender: () => openFirstDm(ap, 'action sender'), openReceiver: () => openFirstDm(b, 'action recipient'),
      openLinked: () => openFirstDm(ad, 'action linked reader'), online });
  } else {
  step = 'native-inline-durable-recovery';
  if (!process.env.PROXION_NATIVE_CAPTION_ONLY) await nativeMediaJourney({ sender: ap, receiver: b, sourceGateway: A, targetGateway: B,
    openSender: () => openFirstDm(ap, 'native inline sender'), openReceiver: () => openFirstDm(b, 'native inline recipient'), online });
  step = 'native-caption-edits';
  await nativeCaptionJourney({ sender: ap, receiver: b, sourceGateway: A, targetGateway: B,
    openSender: () => openFirstDm(ap, 'caption sender'), openReceiver: () => openFirstDm(b, 'caption recipient'), online,
    fixtureQuery: process.env.PROXION_NATIVE_CAPTION_ONLY ? 'native-history-' : 'native-durable-' });
  if (!process.env.PROXION_NATIVE_CAPTION_ONLY || process.env.PROXION_NATIVE_REFERENCE_ONLY) {
    step = 'native-reply-references';
    await nativeReplyJourney({ sender: ap, receiver: b, linked: ad, sourceGateway: A,
      openSender: () => openFirstDm(ap, 'reply sender'), openReceiver: () => openFirstDm(b, 'reply recipient'), online });
    step = 'native-reaction-references';
    await nativeReactionJourney({ sender: ap, receiver: b, linked: ad, sourceGateway: A,
      openSender: () => openFirstDm(ap, 'reaction sender'), openReceiver: () => openFirstDm(b, 'reaction recipient'), online });
    step = 'native-delete-pin-references';
    await nativeActionJourney({ sender: ap, receiver: b, linked: ad, sourceGateway: A, targetGateway: B,
      openSender: () => openFirstDm(ap, 'action sender'), openReceiver: () => openFirstDm(b, 'action recipient'),
      openLinked: () => openFirstDm(ad, 'action linked reader'), online });
    step = 'native-reference-navigation';
    await nativeNavigationJourney({ sender: ap, receiver: b, sourceGateway: A, openSender: () => openFirstDm(ap, 'navigation sender'), online });
    step = 'native-live-feedback';
    await nativeFeedbackJourney({ sender: ap, receiver: b, sourceGateway: A,
      openSender: () => openFirstDm(ap, 'feedback sender'), openReceiver: () => openFirstDm(b, 'feedback recipient'), online });
  }
  }
  if (!process.env.PROXION_NATIVE_CAPTION_ONLY) {
  step = 'native-accepted-inline-adaptation';
  await nativeInlineAdaptationJourney({ sender: ap, receiver: b, sourceGateway: A, targetGateway: B,
    openSender: () => openFirstDm(ap, 'adapted inline sender'), openReceiver: () => openFirstDm(b, 'adapted inline recipient'), online });
  step = 'native-gif-tray';
  await nativeGifJourney({ sender: ap, receiver: b, openSender: () => openFirstDm(ap, 'GIF sender'),
    openReceiver: () => openFirstDm(b, 'GIF recipient'), online });
  for (const size of [2 * 1024 * 1024, 25 * 1024 * 1024 - 16]) {
    step = 'native-chunk-resume-' + size;
    await nativeChunkJourney({ sender: ap, receiver: b, sourceGateway: A, targetGateway: B,
      openSender: () => openFirstDm(ap, 'native chunk sender'), openReceiver: () => openFirstDm(b, 'native chunk recipient'), online, size });
  }


  step = 'native-recipient-decline';
  await nativeDeclineJourney({ sender: ap, receiver: b, sourceGateway: A, targetGateway: B,
    openSender: () => openFirstDm(ap, 'decline sender'), openReceiver: () => openFirstDm(b, 'decline recipient'), online });

  // E5 slice 2: A-primary's SENT message self-synced to A-device. Check A-device's
  // local plaintext store (robust to which thread is active).
  step = 'a-device-self-sync';
  const adGotReply = await ad.waitForFunction(async text => {
    const rows = await (await import('./dmhistory.js')).dmHistorySearch(text);
    return rows.some(row => row.content === text);
  }, { timeout: 20000, polling: 250 }, REPLY).then(() => true, () => false);
  const adFeedReply = await ad.evaluate((t) => (document.getElementById('message-feed')?.textContent || '').includes(t), REPLY);
  if (!adGotReply && !adFeedReply) {
    fail('A-device did NOT receive A-primary\'s sent message via self-sync (not in feed or local store)');
    console.error(`  · A-device console tail:\n${ad._console.slice(-8).map(l => '    ' + l).join('\n')}`);
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

  step = 'durable-fanout-outbox';
  await dmOutboxJourney({ sender: b, receiver: ap, openSender: () => openFirstDm(b, 'B'),
    openReceiver: () => openFirstDm(ap, 'A-primary'), label: 'gateway fanout outbox' });

  // Peer acceptance must not discard a lost own-device acknowledgement.
  step = 'independent-self-sync';
  await ad.goto('about:blank'); await sleep(500);
  const mirrorText = 'independent-mirror-' + Date.now();
  const suppressOwnAck = ownDevice => {
    const send = WebSocket.prototype.send, wrapped = new WeakSet();
    WebSocket.prototype.send = function (data) {
      let frame; try { frame = JSON.parse(data); } catch { /* other frame */ }
      if (frame?.message_id && ['send_dm', 'local_dm', 'send_dm_fanout'].includes(frame.cmd) && !wrapped.has(this)) {
        wrapped.add(this); const receive = this.onmessage;
        this.onmessage = function (event) {
          let reply; try { reply = JSON.parse(event.data); } catch { /* other frame */ }
          if (reply?.type === 'send_dm_fanout_ack' && reply.delivered?.some(row => row.to_device_id === ownDevice)) {
            const delivered = reply.delivered.filter(row => row.to_device_id !== ownDevice);
            if (!delivered.length) return;
            return receive?.call(this, new MessageEvent('message', { data: JSON.stringify({ ...reply, delivered }) }));
          }
          return receive?.call(this, event);
        };
      }
      return send.call(this, data);
    };
  };
  // The connection captures its paced sender at construction. Install before
  // reload so the live socket actually uses this failure-injection hook.
  const ownAckBlocker = await ap.evaluateOnNewDocument(suppressOwnAck, adDid);
  await ap.reload({ waitUntil: 'networkidle2' }); await online(ap);
  const observed = [];
  const wireSession = await ap.createCDPSession(); await wireSession.send('Network.enable');
  wireSession.on('Network.webSocketFrameSent', event => { try { observed.push(JSON.parse(event.response.payloadData)); } catch { /* other frame */ } });
  await openFirstDm(ap, 'mirror sender');
  await ap.focus('#message-input'); await ap.type('#message-input', mirrorText); await ap.keyboard.press('Enter');
  step = 'independent-self-sync-peer-receive';
  await b.waitForFunction(text => document.getElementById('message-feed')?.textContent.includes(text), { timeout: 20000, polling: 250 }, mirrorText);
  step = 'independent-self-sync-pending-mirror';
  await ap.waitForFunction(async text => (await (await import('./e2e.js')).listDmSendJobs()).some(job => job.record.content === text && job.peerConfirmed && !job.confirmed), { timeout: 20000, polling: 250 }, mirrorText).catch(async error => {
    console.error('mirror jobs:', await ap.evaluate(async () => (await (await import('./e2e.js')).listDmSendJobs()).map(job => ({ id: job.id, peerConfirmed: job.peerConfirmed, accepted: job.accepted, entries: job.entries, frames: job.frames?.length }))));
    throw error;
  });
  const mirrorJob = await ap.evaluate(async text => (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.record.content === text), mirrorText);
  const savedMirror = mirrorJob.frames.find(frame => frame.cmd === 'send_dm_fanout').fanout.find(row => row.to_device_id === adDid).payload;
  const afterPeer = observed.length;
  step = 'independent-self-sync-reload';
  await ap.reload({ waitUntil: 'networkidle2' }); await online(ap);
  await ap.waitForFunction(async id => (await (await import('./e2e.js')).listDmSendJobs()).some(job => job.id === id && job.peerConfirmed), { timeout: 15000, polling: 250 }, mirrorJob.id);
  if (A.queued(apDid, adDid) !== 1) throw Error('unconfirmed mirror missing from durable gateway queue');
  step = 'independent-self-sync-gateway-restart';
  await A.restart(); await online(ap);
  if (A.queued(apDid, adDid) !== 1) throw Error('lost-ack mirror did not survive gateway restart');
  step = 'independent-self-sync-recover-ack';
  await ap.removeScriptToEvaluateOnNewDocument(ownAckBlocker.identifier);
  await ap.reload({ waitUntil: 'networkidle2' }); await online(ap);
  await ap.waitForFunction(async id => !(await (await import('./e2e.js')).listDmSendJobs()).some(job => job.id === id), { timeout: 30000, polling: 250 }, mirrorJob.id);
  const retries = observed.slice(afterPeer).filter(frame => frame.message_id === mirrorJob.id);
  if (!retries.length || retries.some(frame => frame.cmd !== 'send_dm_fanout' || frame.fanout.some(row => row.to_device_id !== adDid || JSON.stringify(row.payload) !== JSON.stringify(savedMirror)))) throw Error('mirror retry replayed primary or changed ciphertext');
  await ad.goto(A.url, { waitUntil: 'networkidle2' }); await online(ad); await openFirstDm(ad, 'independent mirror receiver');
  await ad.waitForFunction(text => document.getElementById('message-feed')?.textContent.includes(text), { timeout: 20000, polling: 250 }, mirrorText);
  for (const page of [b, ad]) {
    const count = await page.evaluate(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).filter(row => row.content === text).length, mirrorText);
    if (count !== 1) throw Error('independent mirror recovery duplicated history: ' + count);
  }
  await wireSession.detach(); await openFirstDm(ap, 'A-primary after mirror recovery');
  console.log('  ✓ confirmed peer delivery retains its lost-ack mirror across sender reload and gateway restart; only exact mirror ciphertext retries, one message per device');

  // Accepted offline copies must survive loss of ALL gateway process memory.
  for (const route of ['relayed peer', 'local self-sync']) {
    step = 'durable-offline-' + route;
    await ad.goto('about:blank'); await sleep(500);
    const sender = route === 'relayed peer' ? b : ap;
    const receiver = route === 'relayed peer' ? ap : b;
    const text = 'offline-restart-' + route + '-' + Date.now();
    await openFirstDm(sender, route + ' sender');
    await sleep(1800); // let the existing device-roster lookup finish after reconnect
    await sender.focus('#message-input'); await sender.type('#message-input', text); await sender.keyboard.press('Enter');
    await receiver.waitForFunction(text => document.getElementById('message-feed')?.textContent.includes(text), { timeout: 20000 }, text);
    for (let attempt = 0; attempt < 50 && A.queued(apDid, adDid) !== 1; attempt++) await sleep(100);
    if (A.queued(apDid, adDid) !== 1) throw Error(route + ': accepted offline copy was not committed');
    await A.restart();
    await online(ap);
    await ad.goto(A.url, { waitUntil: 'networkidle2' });
    await ad.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
    await online(ad); await openFirstDm(ad, route + ' recovered device');
    await ad.waitForFunction(text => document.getElementById('message-feed')?.textContent.includes(text), { timeout: 20000 }, text);
    for (let attempt = 0; attempt < 50 && A.queued(apDid, adDid) !== 0; attempt++) await sleep(100);
    if (A.queued(apDid, adDid) !== 0) throw Error(route + ': committed row was not removed');
    await ad.reload({ waitUntil: 'networkidle2' }); await online(ad); await openFirstDm(ad, route + ' history');
    const count = await ad.evaluate(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).filter(row => row.content === text).length, text);
    if (count !== 1) throw Error(route + ': recovery did not retain one exact plaintext message');
    console.log(`  ✓ ${route}: accepted offline fanout survives SIGKILL/restart and restores one readable device message`);
    await openFirstDm(ap, 'A-primary after restart');
  }

  // ── Revoke A-device from Settings > Linked devices: it must be signed out,
  // not just dropped from the list (it used to stay connected and reappear).
  step = 'revoke-device';
  await ap.evaluate(() => document.getElementById('settings-btn').click());
  const revokeSel = `#settings-devices-list [data-device-id="${adDid}"]`;
  await ap.waitForFunction((sel) => !!document.querySelector(sel), { timeout: 10000 }, revokeSel)
    .catch(() => fail('A-device is not listed under Linked devices'));
  if (process.exitCode) throw new Error('stop');
  await ap.evaluate((sel) => document.querySelector(sel).click(), revokeSel);
  await ap.waitForFunction(() => getComputedStyle(document.getElementById('confirm-modal')).display !== 'none', { timeout: 5000 });
  await ap.evaluate(() => document.getElementById('confirm-ok').click());
  await ad.waitForFunction(() => /revoked from another device/i.test(document.body.textContent), { timeout: 10000 })
    .catch(() => fail('A-device was never told it was revoked'));
  await ap.waitForFunction((sel) => !document.querySelector(sel), { timeout: 10000 }, revokeSel)
    .catch(() => fail('A-device is still listed after Revoke'));
  await sleep(6000);   // past the device's reconnect attempt
  if (await ad.evaluate(() => document.querySelector('.dot')?.classList.contains('online'))) {
    fail('A-device reconnected as the account after it was revoked');
  }
  await ap.evaluate(() => document.getElementById('settings-btn').click());
  await sleep(1500);
  if (await ap.evaluate((sel) => !!document.querySelector(sel), revokeSel)) {
    fail('A-device came back in Linked devices after it was revoked');
  }

  step = 'native-voice-byte-audit';
  await auditNativeVoiceHistory([ap, b]);

  step = 'native-policy-block';
  await nativeBlockJourney({ sender: ap, receiver: b, sourceGateway: A, targetGateway: B,
    openSender: () => openFirstDm(ap, 'blocked sender'), online });

  } // Full acceptance suite; focused caption runs still use both real gateways and primary browsers.
  step = 'done';
  if (pageErrors.length) fail(`uncaught page errors: ${pageErrors.join(' | ')}`);
  if (!process.exitCode) {
    console.log(`  ✓ multi-device DM OK — A-device paired to account A; B's cross-gateway E2E DM`);
    console.log(`    decrypted on BOTH A-primary AND A-device (cross-gateway device fanout);`);
    console.log(`    A-primary's REPLY reached B; self-synced to A-device; history survived reload.`);
  }
} catch (e) {
  if (e.message !== 'stop') console.error(`  ✗ [${step}] threw: ${(process.env.PROXION_NATIVE_NAV_ONLY || process.env.PROXION_NATIVE_FEEDBACK_ONLY) ? e.stack : e.message}`);
  if (!process.exitCode) process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  for (const p of procs) { try { p.kill(); } catch {} }
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch {} }
}
