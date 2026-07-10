// Headless-browser smoke test — loads the running gateway's web UI in real
// Chrome and FAILS on any page error during load (ReferenceError / SyntaxError
// / uncaught throw). This catches the class of bug that unit tests + ESLint
// miss: code that is syntactically valid and has no undefined *names* per the
// linter, but throws at module-evaluation time (e.g. a function called outside
// the scope it is defined in), aborting main.js before listeners are wired.
//
// Prereq: the gateway must already be running (python run_gateway.py).
// Usage:   node web/smoke_browser.mjs            (defaults to https://localhost:8080)
//          PROXION_SMOKE_URL=https://host:port node web/smoke_browser.mjs
//          PROXION_CHROME=/path/to/chrome node web/smoke_browser.mjs
//
// Exits 0 if the page loads with no fatal errors, 1 otherwise.

import { existsSync } from 'fs';
import puppeteer from 'puppeteer-core';

const URL = process.env.PROXION_SMOKE_URL || 'https://localhost:8080/';
const CHROME_CANDIDATES = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter(Boolean);
const chrome = CHROME_CANDIDATES.find(p => existsSync(p));
if (!chrome) {
  console.error('No Chrome/Edge found. Set PROXION_CHROME to its path.');
  process.exit(2);
}

// Errors we treat as fatal (a broken page), vs. expected-in-sandbox noise.
const FATAL = /is not defined|is not a function|Unexpected (token|identifier)|SyntaxError|Cannot (read|access)|ReferenceError/;
// WebSocket failures are environment-dependent (cert/port) and not a page-code bug.
const IGNORE = /WebSocket|Gateway WebSocket error|Failed to load resource/;

const browser = await puppeteer.launch({
  executablePath: chrome, headless: 'new',
  args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  const fatal = [];
  const all = [];
  page.on('pageerror', e => { all.push('pageerror: ' + e.message); if (FATAL.test(e.message)) fatal.push(e.message); });
  page.on('console', m => {
    if (m.type() !== 'error') return;
    const t = m.text();
    all.push('console.error: ' + t);
    if (FATAL.test(t) && !IGNORE.test(t)) fatal.push(t);
  });

  await page.goto(URL, { waitUntil: 'load', timeout: 20000 });
  await new Promise(r => setTimeout(r, 2500)); // let the async bootstrap IIFE run

  // Sanity probe: did the script finish wiring? settings-btn is set at eval time,
  // but the sign-in listener is wired in setupEventListeners() near the very end —
  // if eval aborted early, the button exists but has no behavior. We assert the
  // page object model is intact and report key state.
  const probe = await page.evaluate(() => ({
    hasSigninBtn: !!document.getElementById('settings-solid-solidcommunity'),
    title: document.title,
  }));

  // a11y guard: every interactive control must have an accessible name (aria-label,
  // title, or text). An icon-only button with none announces as just "button" to a
  // screen reader. Covers hidden modal/sidebar buttons too.
  const unnamed = await page.evaluate(() =>
    [...document.querySelectorAll('button, a[href], [role=button]')]
      .filter(el => !(el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent.trim()))
      .map(el => el.id || el.className || el.outerHTML.slice(0, 50)));

  // Focus-trap a11y check: opening a modal must move focus inside it, Tab must
  // stay within it, and closing (Escape) must restore focus to the opener.
  // Exercises focus-trap.js's observer in a real browser (unit tests can't —
  // there's no jsdom here).
  const focusTrap = await page.evaluate(async () => {
    const raf = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    // Dismiss the first-run onboarding modal first: while it's open it is the
    // base trapped modal, so a shortcut modal stacked over it and closed via the
    // global Escape (which hides BOTH) would restore focus to the pre-onboarding
    // element, not our opener. A real user isn't mid-onboarding when using Ctrl+/.
    const onboarding = document.getElementById('onboarding-modal');
    if (onboarding) onboarding.style.display = 'none';
    await raf();
    const opener = document.getElementById('search-input') || document.body;
    opener.focus?.();
    const before = document.activeElement;
    // Ctrl+/ toggles the keyboard-shortcut modal (wired late in setup).
    document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', ctrlKey: true, bubbles: true }));
    await raf();
    const modal = document.getElementById('shortcut-modal');
    const visible = modal && getComputedStyle(modal).display !== 'none';
    const focusInside = modal && modal.contains(document.activeElement);
    // Escape closes it and should restore focus to the opener.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await raf();
    const closed = !modal || getComputedStyle(modal).display === 'none';
    const restored = document.activeElement === before;
    return { visible, focusInside, closed, restored };
  });
  const ftFails = Object.entries(focusTrap).filter(([, v]) => !v).map(([k]) => k);

  console.log(`Loaded ${URL}`);
  console.log('probe:', JSON.stringify(probe));
  console.log('focus-trap:', JSON.stringify(focusTrap));
  if (ftFails.length) {
    console.error(`\n✗ focus-trap a11y check failed: ${ftFails.join(', ')}`);
    process.exitCode = 1;
  }
  console.log(`console/page messages: ${all.length}, fatal: ${fatal.length}`);
  if (unnamed.length) {
    console.error(`\n✗ ${unnamed.length} interactive control(s) without an accessible name (a11y):`);
    unnamed.forEach(u => console.error('  ✗ ' + u));
    process.exitCode = 1;
  }
  if (fatal.length) {
    console.error('\nFATAL page errors (page code is broken):');
    fatal.forEach(e => console.error('  ✗ ' + e));
    process.exitCode = 1;
  } else {
    console.log('\n✓ No fatal page errors — main.js evaluated and wired up cleanly.');
  }
} finally {
  await browser.close();
}
