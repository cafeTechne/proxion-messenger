// Keyboard-only journey smoke (PLAN_ROUND_56 E2) — drives the core journey with
// ONLY the keyboard (no .click(), no mouse): skip-link → composer, Ctrl+K search,
// create a room, send a message, arrow through the sidebar (roving tabindex), and
// open/close the shortcut modal asserting focus is RESTORED to the opener (C2).
// Self-spawns an isolated gateway like the other smokes.
//
//   node web/smoke_keyboard.mjs
//   PROXION_SMOKE_URL=http://127.0.0.1:PORT/ node web/smoke_keyboard.mjs
//
// Exit 0 = every keyboard assertion held. Non-zero = a keyboard path is broken.

import { existsSync, mkdtempSync, rmSync } from 'fs';
import { spawn } from 'child_process';
import { createServer, connect } from 'net';
import { tmpdir } from 'os';
import { join, resolve } from 'path';
import puppeteer from 'puppeteer-core';

setTimeout(() => { console.error('HARD TIMEOUT'); process.exit(3); }, 180000);

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
    c.once('error', () => { c.destroy(); Date.now() > deadline ? rej(new Error(`port ${port} not up`)) : setTimeout(tryOnce, 250); });
  };
  tryOnce();
});
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const procs = [], dirs = [];
async function startGateway() {
  const httpPort = await freePort(), wsPort = await freePort();
  const dataDir = mkdtempSync(join(tmpdir(), 'proxion-kbd-'));
  dirs.push(dataDir);
  const env = {
    ...process.env,
    PROXION_DATA_DIR: dataDir, PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
    PROXION_HOST: '127.0.0.1', PROXION_PUBLIC_URL: '', PROXION_REQUIRE_AUTH: '0',
    PROXION_CSS_URL: '', PROXION_CSS_EMAIL: '', PROXION_CSS_PASSWORD: '',
    PROXION_WEB_DIR: WEB, PROXION_LOG_LEVEL: 'WARNING',
  };
  const p = spawn('python', ['scripts/run_test_gateway.py'], { cwd: REPO, env });
  procs.push(p);
  let log = '';
  p.stdout.on('data', d => log += d); p.stderr.on('data', d => log += d);
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && !/PROXION_GATEWAY_READY/.test(log)) {
    if (p.exitCode !== null) throw new Error('gateway exited:\n' + log.slice(-500));
    await sleep(300);
  }
  await waitForPort(httpPort, 15000);
  return `http://127.0.0.1:${httpPort}/`;
}

// ── Keyboard-only primitives ────────────────────────────────────────────────
const active = (page) => page.evaluate(() => {
  const a = document.activeElement;
  return a ? { id: a.id, cls: (a.className || '').toString(), tag: a.tagName,
    inFeed: !!a.closest?.('#message-feed'), inNav: !!a.closest?.('nav li') } : null;
});

// Press Tab (or Shift+Tab) up to `max` times until the focused element satisfies
// `pred(activeInfo)`. Returns true if reached. Pure keyboard — no focus() calls.
async function tabTo(page, pred, { max = 60, shift = false } = {}) {
  for (let i = 0; i < max; i++) {
    const a = await active(page);
    if (a && pred(a)) return true;
    if (shift) { await page.keyboard.down('Shift'); await page.keyboard.press('Tab'); await page.keyboard.up('Shift'); }
    else await page.keyboard.press('Tab');
    await sleep(30);
  }
  const a = await active(page);
  return !!(a && pred(a));
}

let failures = 0;
const check = (name, ok) => { console.log(`  ${ok ? '✓' : '✗'} ${name}`); if (!ok) failures++; };

let browser = null;
try {
  const url = process.env.PROXION_SMOKE_URL || await startGateway();
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  page.on('dialog', d => d.accept());
  await page.setViewport({ width: 1280, height: 800 });
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 }).catch(() => {});
  await sleep(400);

  // 1. First-run onboarding is a required gate: Escape must NOT dismiss it
  //    (Escape closes only the topmost non-persistent dialog). Then walk it to
  //    the end with the keyboard alone: focus must start on a real control (a
  //    hidden one used to win the trap's first slot and drop focus to <body>).
  await page.keyboard.press('Escape');
  await sleep(200);
  const onboardingKept = await page.evaluate(() => {
    const m = document.getElementById('onboarding-modal');
    return !m || getComputedStyle(m).display !== 'none';
  });
  check('Escape leaves the onboarding gate open', onboardingKept);
  check('onboarding opens with focus on Get started', (await active(page))?.id === 'ob-start-btn');
  const obInDialog = () => page.evaluate(() => document.getElementById('onboarding-modal').contains(document.activeElement));
  let obTrapped = true;
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press('Tab'); await sleep(30);
    if (!(await obInDialog())) obTrapped = false;
  }
  check('Tab cycles inside the onboarding dialog', obTrapped);
  check('Tab reaches Get started', await tabTo(page, a => a.id === 'ob-start-btn', { max: 10 }));
  await page.keyboard.press('Enter');
  await sleep(300);
  check('Tab reaches the name field', await tabTo(page, a => a.id === 'ob-name', { max: 10 }));
  await page.keyboard.type('Kim');
  check('Tab reaches Continue', await tabTo(page, a => a.id === 'ob-step2-btn', { max: 10 }));
  await page.keyboard.press('Enter');
  await sleep(300);
  check('Tab reaches Start now', await tabTo(page, a => a.id === 'ob-skip-pod', { max: 20 }));
  await page.keyboard.press('Enter');
  await sleep(400);
  // "Start now" asks for confirmation in the shared confirm dialog, stacked
  // over the onboarding gate.
  check('the no-pod confirm takes focus',
    await page.evaluate(() => document.getElementById('confirm-modal').contains(document.activeElement)));
  check('Tab reaches the no-pod Confirm', await tabTo(page, a => a.id === 'confirm-ok', { max: 6 }));
  await page.keyboard.press('Enter');
  await sleep(400);
  check('Tab reaches Skip for now', await tabTo(page, a => a.id === 'ob-step5-skip', { max: 20 }));
  await page.keyboard.press('Enter');
  await sleep(300);
  check('Tab reaches Finish', await tabTo(page, a => a.id === 'ob-finish-btn', { max: 20 }));
  await page.keyboard.press('Enter');
  await sleep(600);
  const onboardingDone = await page.evaluate(() => {
    const m = document.getElementById('onboarding-modal');
    return !m || getComputedStyle(m).display === 'none';
  });
  check('onboarding completed by keyboard alone', onboardingDone);
  if (!onboardingDone) {
    await page.evaluate(() => {
      const m = document.getElementById('onboarding-modal');
      if (m) m.style.display = 'none';
    });
  }
  await sleep(200);

  // 2. Skip link is the first tab stop, and activating it focuses the composer.
  await page.evaluate(() => document.body.focus());
  await page.keyboard.press('Tab');
  const firstStop = await active(page);
  check('skip link is the first Tab stop', !!firstStop && /skip-nav/.test(firstStop.cls));

  // 3. Ctrl+K focuses the search box.
  await page.keyboard.down('Control'); await page.keyboard.press('k'); await page.keyboard.up('Control');
  await sleep(120);
  check('Ctrl+K focuses search', (await active(page))?.id === 'search-input');

  // 4. Create a room entirely by keyboard: Tab to the create button, Enter, type
  //    the name into the trapped modal input, Tab to the submit control, Enter.
  const reachedCreate = await tabTo(page, a => a.id === 'create-room-btn');
  check('Tab reaches the create-room button', reachedCreate);
  await page.keyboard.press('Enter');
  await sleep(400);
  const atRoomInput = await tabTo(page, a => a.id === 'room-name-input', { max: 20 });
  check('focus lands in the room-name field', atRoomInput);
  await page.keyboard.type('general');
  const atSubmit = await tabTo(page, a => a.id === 'room-create-submit', { max: 20 });
  check('Tab reaches the create submit button', atSubmit);
  await page.keyboard.press('Enter');
  await sleep(800);
  const roomExists = await page.evaluate(() => Array.from(document.querySelectorAll('nav li'))
    .some(li => /general/i.test(li.textContent || '')));
  check('room created via keyboard', roomExists);
  // The busy submit keeps focus (aria-disabled, not disabled), and once the
  // form gives way to the result its Done button takes over.
  check('focus stays in the dialog after the busy submit', (await active(page))?.id === 'room-create-done-btn');
  await page.keyboard.press('Escape'); // close the "created" confirmation if shown
  await sleep(200);
  check('closing the dialog returns focus to its opener',
    (await active(page))?.id === 'create-room-btn');

  // 5. Send a message: focus the composer by keyboard, type, Enter.
  const atComposer = await tabTo(page, a => a.id === 'message-input');
  check('Tab reaches the composer', atComposer);
  await page.keyboard.type('hello from the keyboard');
  await page.keyboard.press('Enter');
  await sleep(500);
  const messageSent = await page.evaluate(() => Array.from(document.querySelectorAll('#message-feed .message'))
    .some(m => /hello from the keyboard/.test(m.textContent || '')));
  check('message sent with Enter', messageSent);

  // 5b. Content inside a message is reachable: the feed is one tab stop with
  //     arrows between messages, and Tab from the current message walks its
  //     links and buttons (the roving tabindex used to demote all of them).
  await page.keyboard.type('see https://example.com/kbd for details');
  await page.keyboard.press('Enter');
  await sleep(500);
  const inFeed = (a) => a && a.inFeed;
  check('Shift+Tab from the composer reaches the feed', await tabTo(page, inFeed, { shift: true, max: 20 }));
  await page.keyboard.press('End');
  await sleep(80);
  const lastMsg = await page.evaluate(() => {
    const a = document.activeElement;
    const ids = (a.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
    return { isMsg: a.classList.contains('message'),
      described: ids.map(id => document.getElementById(id)?.textContent || '').join(' ') };
  });
  check('End lands on the last message', lastMsg.isMsg);
  check('the focused message describes its content, not only sender and time', /example\.com\/kbd/.test(lastMsg.described));
  await page.keyboard.press('Tab');
  await sleep(60);
  check('Tab from a message reaches the link in it',
    await page.evaluate(() => document.activeElement?.tagName === 'A' && /example\.com\/kbd/.test(document.activeElement.href)));
  // An image in the current message opens the lightbox, which traps focus and
  // hands it back to the image on Escape. The image button is planted in the
  // message (uploading a file is outside this smoke's scope).
  await page.evaluate(() => {
    const msgs = document.querySelectorAll('#message-feed .message');
    const body = msgs[msgs.length - 1].querySelector('.msg-content');
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'msg-image-btn'; b.id = 'kbd-img-btn';
    b.innerHTML = '<img class="msg-image-preview" alt="pixel" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">';
    body.appendChild(b);
  });
  await sleep(100);
  check('Tab reaches an image in the message', await tabTo(page, a => a.id === 'kbd-img-btn', { max: 6 }));
  await page.keyboard.press('Enter');
  await sleep(300);
  const lbOpen = () => page.evaluate(() => getComputedStyle(document.getElementById('lightbox')).display !== 'none');
  check('Enter on the image opens the lightbox', await lbOpen());
  let lbTrapped = await page.evaluate(() => document.getElementById('lightbox').contains(document.activeElement));
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('Tab'); await sleep(30);
    if (!(await page.evaluate(() => document.getElementById('lightbox').contains(document.activeElement)))) lbTrapped = false;
  }
  check('focus stays inside the lightbox', lbTrapped);
  await page.keyboard.press('Escape');
  await sleep(300);
  check('Escape closes the lightbox', !(await lbOpen()));
  check('closing the lightbox returns focus to the image', (await active(page))?.id === 'kbd-img-btn');

  // 6. Roving tabindex: Tab onto a sidebar row, ArrowDown moves focus to another
  //    row (the whole list is one tab stop — Phase B). The focused element is
  //    a named button, not a bare listitem.
  const atRow = await tabTo(page, a => a.inNav);
  check('Tab reaches a sidebar row', atRow);
  const rowAx = await page.evaluate(() => ({ role: document.activeElement.getAttribute('role'),
    name: (document.activeElement.textContent || '').replace(/\s+/g, ' ').trim() }));
  check(`the focused sidebar row is a named button (${rowAx.role}: ${rowAx.name.slice(0, 30)})`,
    rowAx.role === 'button' && /general/.test(rowAx.name));
  await page.keyboard.press('ArrowDown');
  await sleep(120);
  const afterArrow = await active(page);
  check('ArrowDown moves within the sidebar list', !!afterArrow && afterArrow.inNav);

  // 6b. Add contact: the Scan QR control is a real tab stop.
  check('Tab reaches the add-contact button', await tabTo(page, a => a.id === 'add-peer-btn'));
  await page.keyboard.press('Enter');
  await sleep(300);
  check('Tab reaches Scan QR code', await tabTo(page, a => a.id === 'scan-qr-btn', { max: 10 }));
  await page.keyboard.press('Escape');
  await sleep(300);

  // 7. Shortcut modal open (Ctrl+/) then Escape must RESTORE focus to the opener.
  //    Park focus on a known control first so we can assert the restore target.
  await tabTo(page, a => a.id === 'message-input');
  await page.keyboard.down('Control'); await page.keyboard.press('/'); await page.keyboard.up('Control');
  await sleep(300);
  const modalOpen = await page.evaluate(() => getComputedStyle(document.getElementById('shortcut-modal')).display !== 'none');
  check('Ctrl+/ opens the shortcut modal', modalOpen);
  const inertWhileOpen = await page.evaluate(() => ({
    app: document.getElementById('app').inert,
    dialog: document.getElementById('shortcut-modal').inert,
    toasts: document.getElementById('toast-container').inert,
  }));
  check('the page behind the open modal is inert', inertWhileOpen.app === true);
  check('the modal itself and the toasts stay live', !inertWhileOpen.dialog && !inertWhileOpen.toasts);
  // Record every focus move during the close: focus must go straight from the
  // dialog to the opener, never through <body> (a focusout with no target).
  await page.evaluate(() => {
    window.__focusLog = [];
    document.addEventListener('focusout', (e) => {
      window.__focusLog.push(e.relatedTarget ? (e.relatedTarget.id || e.relatedTarget.tagName) : 'BODY');
    }, true);
  });
  await page.keyboard.press('Escape');
  await sleep(300);
  const modalClosed = await page.evaluate(() => getComputedStyle(document.getElementById('shortcut-modal')).display === 'none');
  check('Escape closes the shortcut modal', modalClosed);
  check('focus restored to the opener (message-input)', (await active(page))?.id === 'message-input');
  const focusLog = await page.evaluate(() => window.__focusLog);
  check(`focus never dropped to <body> while closing (${focusLog.join(' > ') || 'none'})`, !focusLog.includes('BODY'));
  check('the page is live again after the modal closes',
    await page.evaluate(() => !document.getElementById('app').inert && !document.querySelector('[data-trap-inert]')));

  // 8. Confirm dialogs: leave the room from the room-options menu and confirm
  //    with the keyboard. The confirm's hidden "also delete" checkbox used to
  //    count as the first tab stop, so focus fell to <body> and Tab never got
  //    to Confirm.
  check('Tab reaches room options', await tabTo(page, a => a.id === 'room-options-btn'));
  await page.keyboard.press('Enter');
  await sleep(300);
  let atLeave = false;
  for (let i = 0; i < 15 && !atLeave; i++) {
    atLeave = await page.evaluate(() => /leave/i.test(document.activeElement?.textContent || ''));
    if (!atLeave) { await page.keyboard.press('ArrowDown'); await sleep(40); }
  }
  check('arrow keys reach Leave room', atLeave);
  await page.keyboard.press('Enter');
  await sleep(400);
  const confirmShown = () => page.evaluate(() => getComputedStyle(document.getElementById('confirm-modal')).display !== 'none');
  check('the confirm dialog opens', await confirmShown());
  const confirmFocus = await active(page);
  check(`confirm opens with focus inside it (${confirmFocus?.id || confirmFocus?.tag})`,
    await page.evaluate(() => document.getElementById('confirm-modal').contains(document.activeElement)));
  check('Tab reaches Confirm', await tabTo(page, a => a.id === 'confirm-ok', { max: 6 }));
  check('Shift+Tab reaches Cancel', await tabTo(page, a => a.id === 'confirm-cancel', { max: 6, shift: true }));
  check('Tab wraps back to Confirm', await tabTo(page, a => a.id === 'confirm-ok', { max: 6 }));
  await page.keyboard.press('Enter');
  await sleep(800);
  check('Enter on Confirm closes the dialog', !(await confirmShown()));
  const roomGone = await page.evaluate(() => !Array.from(document.querySelectorAll('nav li'))
    .some(li => /general/i.test(li.textContent || '')));
  check('the room was left', roomGone);

} catch (e) {
  console.error('  ✗ threw: ' + (e.stack || e.message));
  failures += 1;
} finally {
  if (browser) await browser.close();
  for (const p of procs) { try { p.kill(); } catch { /* ignore */ } }
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ } }
}

if (failures === 0) console.log('✓ keyboard: full journey operable by keyboard alone.');
else console.error(`✗ keyboard: ${failures} assertion(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
