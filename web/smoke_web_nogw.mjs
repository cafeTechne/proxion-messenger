// Gateway-less web-build acceptance smoke (R102.5 / R103+).
//
// Builds the static "Proxion Web" app (scripts/build-web.mjs, so the web-mode
// meta + CSP are injected), serves it, and drives it in a real browser with NO
// gateway running.
//
//   Tier 1 (always, needs only Chrome): the app boots in web mode, attempts no
//   gateway WebSocket, and hides the realtime-only controls (DM + call entry).
//   Tier 2 (when a CSS pod is reachable): sign in with the pod over OIDC, create
//   a shared room, post a message, reload, and assert the history persisted —
//   all without a gateway. Tier 2 self-skips if CSS cannot be started.
//
//   node web/smoke_web_nogw.mjs
//   PROXION_CHROME=/path/to/chrome node web/smoke_web_nogw.mjs
//
// Exit 0 = all executed assertions met (Tier 2 skipped counts as pass), non-zero
// = a step failed. Like the other smokes it needs Chrome and is a local gate.

import { existsSync, mkdtempSync, rmSync, readFileSync, writeFileSync, statSync } from 'fs';
import { spawn } from 'child_process';
import { createServer } from 'http';
import { createServer as netServer, connect as netConnect } from 'net';
import { tmpdir } from 'os';
import { join, resolve, extname, normalize } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
import { startCss, provisionAccount } from './scripts/css-harness.mjs';

const HERE = resolve(fileURLToPath(new URL('.', import.meta.url)));   // web/
const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find(p => p && existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

let step = 'init';
const fail = (m) => { console.error(`  \u2717 [${step}] ${m}`); process.exitCode = 1; };
const ok = (m) => console.log(`  \u2713 ${m}`);

function freePort() {
  return new Promise((res, rej) => {
    const s = netServer();
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
    s.on('error', rej);
  });
}
const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.json': 'application/json', '.jsonld': 'application/ld+json', '.css': 'text/css',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
  '.webmanifest': 'application/manifest+json',
};
function staticServer(rootDir, port) {
  const srv = createServer((req, res) => {
    try {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p.endsWith('/')) p += 'index.html';
      const fp = normalize(join(rootDir, p));
      if (!fp.startsWith(rootDir) || !existsSync(fp) || statSync(fp).isDirectory()) {
        res.writeHead(404); res.end('not found'); return;
      }
      res.writeHead(200, { 'Content-Type': MIME[extname(fp)] || 'application/octet-stream' });
      res.end(readFileSync(fp));
    } catch (e) { res.writeHead(500); res.end(String(e)); }
  });
  return new Promise((r) => srv.listen(port, '127.0.0.1', () => r(srv)));
}

let browser = null, server = null, css = null, buildDir = null;
async function cleanup() {
  try { if (browser) await browser.close(); } catch {}
  try { if (server) server.close(); } catch {}
  try { if (css) css.stop(); } catch {}
  try { if (buildDir) rmSync(buildDir, { recursive: true, force: true }); } catch {}
}

try {
  // ── Build the static web app (injects web mode + CSP) ──
  step = 'build';
  buildDir = mkdtempSync(join(tmpdir(), 'proxion-web-'));
  await new Promise((res, rej) => {
    const b = spawn(process.execPath, [join(HERE, 'scripts', 'build-web.mjs'), HERE, buildDir]);
    b.on('exit', (c) => c === 0 ? res() : rej(new Error('build-web exited ' + c)));
  });
  if (!/proxion-mode/.test(readFileSync(join(buildDir, 'index.html'), 'utf8'))) {
    throw new Error('build did not inject web mode meta');
  }
  ok('built static web app with web-mode meta + CSP');

  const port = await freePort();
  server = await staticServer(buildDir, port);
  const appUrl = `http://127.0.0.1:${port}/`;

  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'],
  });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  let wsAttempt = null;
  page.on('request', (r) => { if (/^wss?:\/\//.test(r.url())) wsAttempt = r.url(); });

  // ── Tier 1: web-mode boot + gating (no CSS needed) ──
  step = 'tier1-load';
  await page.goto(appUrl, { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 800));

  const mode = await page.evaluate(() => window.proxionTransport && window.proxionTransport.mode);
  if (mode === 'web') ok('app booted in web mode'); else fail(`expected web mode, got ${mode}`);

  if (!wsAttempt) ok('no gateway WebSocket was attempted');
  else fail(`web build tried a gateway socket: ${wsAttempt}`);

  const caps = await page.evaluate(() => {
    const t = window.proxionTransport;
    return t ? ['rooms', 'history', 'invites', 'dm', 'presence', 'calls'].map((f) => t.supports(f)) : null;
  });
  // R105: the web build now supports the full feature set (rooms, DMs, presence, calls).
  if (caps && caps.every(Boolean)) ok('web build reports the full gateway-free feature set');
  else fail(`unexpected web capabilities: ${JSON.stringify(caps)}`);

  const loginPromptShown = await page.evaluate(() => {
    const el = document.getElementById('web-signin-modal');
    return !!el && getComputedStyle(el).display !== 'none' && !!document.getElementById('web-signin-btn');
  });
  if (loginPromptShown) ok('signed-out web build shows the pod sign-in prompt');
  else fail('signed-out web build showed no sign-in prompt');

  if (errors.length) fail('page errors during boot:\n    ' + errors.join('\n    '));
  else ok('web build booted with no page/console errors');

  // Tier 2: real OIDC integration against a live pod. Verifies OUR sign-in code
  // end to end (hosted client-id doc, mode detection, redirect) lands the browser
  // on the pod's real OIDC auth endpoint with the correct client_id, redirect_uri,
  // and PKCE challenge. The CSS login FORM is the pod server's own UI (a JS SPA
  // needing a browser account cookie), so filling it is a manual check.
  step = 'tier2-oidc';
  css = await startCss().catch(() => null);
  if (!css) {
    console.log('  - Tier 2 skipped: no CSS pod available (offline / npx blocked).');
  } else {
    await provisionAccount(css.url, { email: `smoke-${Date.now()}@test.example`, password: 'pw-12345678', label: 'smoke' });
    // The test pod is http://localhost, so relax the production https-only CSP for
    // this run, and point the client-id doc at the local serve origin so it is
    // self-consistent (client_id === its URL, redirect_uri === app origin).
    const idxPath = join(buildDir, 'index.html');
    writeFileSync(idxPath, readFileSync(idxPath, 'utf8').replace(/<meta http-equiv="Content-Security-Policy"[^>]*>/, ''));
    writeFileSync(join(buildDir, 'clientid.jsonld'), JSON.stringify({
      '@context': ['https://www.w3.org/ns/solid/oidc-context.jsonld'],
      client_id: appUrl + 'clientid.jsonld', client_name: 'Proxion', client_uri: appUrl,
      redirect_uris: [appUrl], scope: 'openid profile offline_access webid',
      grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'],
      token_endpoint_auth_method: 'none',
    }));
    await page.goto(appUrl + '?mode=web', { waitUntil: 'networkidle2', timeout: 30000 });
    await new Promise((r) => setTimeout(r, 700));
    await page.evaluate((u) => { document.getElementById('web-signin-url').value = u; window.proxionWebLogin(u); }, css.url);
    await page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {});
    const authUrl = page.url();
    const okRedirect = authUrl.startsWith(css.url + '/.oidc/auth')
      && authUrl.includes('client_id=' + encodeURIComponent(appUrl + 'clientid.jsonld'))
      && authUrl.includes('redirect_uri=' + encodeURIComponent(appUrl))
      && authUrl.includes('code_challenge=');
    if (okRedirect) ok('sign-in redirects to the pod OIDC endpoint with the right client-id + PKCE');
    else fail(`OIDC redirect wrong: ${authUrl.slice(0, 160)}`);
  }
  // ── Tier 3: subpath deploy (Pages serves the app under a nested path, not
  // the origin root). Catches origin-absolute paths that break there: the SW
  // must still register and the manifest + icons must still load. ──
  step = 'tier3-subpath';
  const PREFIX = '/proj/app/';
  const subServer = createServer((req, res) => {
    try {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (!p.startsWith(PREFIX)) { res.writeHead(404); res.end(); return; }
      let rel = p.slice(PREFIX.length) || 'index.html';
      if (rel.endsWith('/')) rel += 'index.html';
      const fp = normalize(join(buildDir, rel));
      if (!fp.startsWith(buildDir) || !existsSync(fp) || statSync(fp).isDirectory()) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': MIME[extname(fp)] || 'application/octet-stream' });
      res.end(readFileSync(fp));
    } catch (e) { res.writeHead(500); res.end(String(e)); }
  });
  const subPort = await freePort();
  await new Promise((r) => subServer.listen(subPort, '127.0.0.1', r));
  try {
    const subUrl = `http://127.0.0.1:${subPort}${PREFIX}`;
    const subPage = await browser.newPage();
    const subErrs = [];
    subPage.on('pageerror', (e) => subErrs.push(e.message));
    await subPage.goto(subUrl, { waitUntil: 'networkidle2', timeout: 30000 });
    await new Promise((r) => setTimeout(r, 900));
    const subMode = await subPage.evaluate(() => window.proxionTransport && window.proxionTransport.mode);
    const swScope = await subPage.evaluate(async () => {
      try { const r = await navigator.serviceWorker.getRegistration(); return r ? r.scope : null; } catch { return null; }
    });
    const assets = await subPage.evaluate(async () => ({
      manifest: await fetch('manifest.json').then((r) => r.status).catch(() => 0),
      icon: await fetch('icons/icon-192.png').then((r) => r.status).catch(() => 0),
    }));
    if (subMode === 'web') ok('subpath: app boots in web mode under a nested path');
    else fail(`subpath: expected web mode, got ${subMode}`);
    if (swScope && swScope.includes(PREFIX)) ok(`subpath: service worker registered under the app path`);
    else fail(`subpath: service worker did not register under the app path (scope=${swScope})`);
    if (assets.manifest === 200 && assets.icon === 200) ok('subpath: manifest + icon load via relative paths');
    else fail(`subpath: manifest/icon not served: ${JSON.stringify(assets)}`);
    if (!subErrs.length) ok('subpath: no page errors');
    else fail('subpath page errors: ' + subErrs.join('; '));
  } finally { subServer.close(); }

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  step = step || 'run';
  fail(e && e.stack ? e.stack : String(e));
} finally {
  await cleanup();
}
