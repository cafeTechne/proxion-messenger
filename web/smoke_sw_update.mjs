// Service worker deploy check: the first load after a deploy must not run the
// new index.html with the old cached scripts (a removed element id makes the
// old main.js throw), and offline must still serve a consistent shell.
//
// Serves a two-version stand-in app (index.html + main.js + style.css that
// each carry a version) with the real sw.js. 127.0.0.2 is a secure context
// but not one of the hosts sw.js skips caching on, so the worker caches as it
// does in production. Assets are served with a CDN-style max-age.
//
//   node web/smoke_sw_update.mjs
// Needs Chrome (or PROXION_CHROME). Self-skips if 127.0.0.2 cannot be bound.

import { existsSync, readFileSync } from 'fs';
import { createServer } from 'http';
import { resolve, join } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';

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

const SW_SRC = readFileSync(join(HERE, 'sw.js'), 'utf8');
let version = 1;
let offline = false;
function files(v) {
  return {
    '/': ['text/html', `<!doctype html><meta charset="utf-8"><meta name="app-ver" content="${v}">`
      + `<link rel="stylesheet" href="style.css"><script src="main.js"></script>`
      + `<script>navigator.serviceWorker.register("sw.js")</script>`],
    '/style.css': ['text/css', `:root { --app-ver: "${v}"; }`],
    '/main.js': ['text/javascript', `window.__jsVer = "${v}";`],
    // A new deploy ships a new worker (the cache name bump).
    '/sw.js': ['text/javascript', SW_SRC.replace(/proxion-shell-v\d+/, `proxion-shell-smoke${v}`)],
  };
}

const srv = createServer((req, res) => {
  if (offline) { req.socket.destroy(); return; }
  const path = req.url.split('?')[0].replace(/^\/index\.html$/, '/');
  const f = files(version)[path];
  if (!f) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, {
    'Content-Type': f[0],
    // Pages-style caching for assets; the worker script itself is revalidated.
    'Cache-Control': path === '/sw.js' ? 'no-cache' : 'max-age=600',
  });
  res.end(f[1]);
});

let browser = null;
async function cleanup() {
  try { if (browser) await browser.close(); } catch { /**/ }
  try { srv.close(); } catch { /**/ }
}

const versions = (page) => page.evaluate(() => ({
  html: document.querySelector('meta[name="app-ver"]')?.content,
  js: window.__jsVer,
  css: getComputedStyle(document.documentElement).getPropertyValue('--app-ver').trim().replace(/"/g, ''),
  controlled: !!navigator.serviceWorker.controller,
}));

try {
  step = 'serve';
  const bound = await new Promise((r) => { srv.once('error', () => r(false)); srv.listen(0, '127.0.0.2', () => r(true)); });
  if (!bound) { console.log('  \u2013 skipped: cannot bind 127.0.0.2 on this machine.'); process.exit(0); }
  const origin = `http://127.0.0.2:${srv.address().port}/`;

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));

  step = 'install v1';
  await page.goto(origin, { waitUntil: 'load' });
  await page.waitForFunction(() => !!navigator.serviceWorker.controller, { timeout: 15000 });
  await page.reload({ waitUntil: 'load' });
  const v1 = await versions(page);
  if (v1.controlled && v1.html === '1' && v1.js === '1') ok('v1 installed and controlling the page');
  else fail(`v1 not installed: ${JSON.stringify(v1)}`);

  step = 'deploy v2';
  version = 2;
  await page.reload({ waitUntil: 'load' });
  const first = await versions(page);
  if (first.html === '2' && first.js === '2' && first.css === '2') ok('first load after the deploy runs v2 HTML with v2 scripts and styles');
  else fail(`first load after the deploy mixed versions: ${JSON.stringify(first)}`);

  // Let the v2 worker install and take over, then reload once more.
  await page.waitForFunction(async () => {
    const r = await navigator.serviceWorker.getRegistration();
    const keys = await caches.keys();
    return !!(r && r.active && !r.installing && !r.waiting) && keys.length === 1 && keys[0] === 'proxion-shell-smoke2';
  }, { timeout: 15000 });
  await sleep(500);
  await page.reload({ waitUntil: 'load' });
  const second = await versions(page);
  if (second.html === '2' && second.js === '2') ok('second load is v2');
  else fail(`second load: ${JSON.stringify(second)}`);

  step = 'offline';
  offline = true;
  await page.reload({ waitUntil: 'load' }).catch(() => null);
  const off = await versions(page);
  if (off.html === '2' && off.js === '2' && off.css === '2') ok('offline reload serves the v2 shell from the cache, consistently');
  else fail(`offline reload: ${JSON.stringify(off)}`);

  if (errors.length) fail('page errors: ' + errors.slice(0, 5).join(' | '));
  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
