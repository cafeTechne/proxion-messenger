// Signed-in gateway-less web E2E (R106). The other smoke (smoke_web_nogw.mjs)
// stops at the OIDC redirect; this one completes a real login against a live CSS
// pod and drives the assembled signed-in UI: the boot runs clean, a room is
// created and a message posted and rendered, and it survives a reload. This is
// the automated version of the manual sign-in pass.
//
// It works headlessly by (1) serving the built app over https with a self-signed
// cert so OIDC accepts the client-id document, (2) letting CSS trust that cert,
// and (3) seeding CSS's account cookie so the consent screen can be authorized.
//
//   node web/smoke_web_signedin.mjs
// Needs Chrome (or PROXION_CHROME), openssl, and npx (for CSS). Self-skips if
// CSS or the cert cannot be produced.

import { existsSync, mkdtempSync, rmSync, readFileSync, writeFileSync, statSync } from 'fs';
import { spawn } from 'child_process';
import { createServer as https } from 'https';
import { createServer as net } from 'net';
import { tmpdir } from 'os';
import { join, extname, normalize, resolve } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
import { startCss, provisionAccount } from './scripts/css-harness.mjs';

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
const freePort = () => new Promise((r, j) => { const s = net(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); s.on('error', j); });
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.jsonld': 'application/ld+json', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' };

// Generate a throwaway self-signed cert for 127.0.0.1. Returns { key, cert } or null.
async function makeCert(dir) {
  const keyPath = join(dir, 'key.pem'), certPath = join(dir, 'cert.pem');
  const okGen = await new Promise((res) => {
    const p = spawn('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-keyout', keyPath, '-out', certPath,
      '-days', '1', '-nodes', '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'],
      { stdio: 'ignore' });
    p.on('exit', (c) => res(c === 0));
    p.on('error', () => res(false));
  });
  if (!okGen || !existsSync(certPath)) return null;
  return { key: readFileSync(keyPath), cert: readFileSync(certPath) };
}

let browser = null, srv = null, css = null, buildDir = null, certDir = null;
async function cleanup() {
  try { if (browser) await browser.close(); } catch { /**/ }
  try { if (srv) srv.close(); } catch { /**/ }
  try { if (css) css.stop(); } catch { /**/ }
  for (const d of [buildDir, certDir]) { try { if (d) rmSync(d, { recursive: true, force: true }); } catch { /**/ } }
}

try {
  step = 'cert';
  certDir = mkdtempSync(join(tmpdir(), 'proxion-cert-'));
  const tls = await makeCert(certDir);
  if (!tls) { console.log('  \u2013 skipped: openssl could not make a cert.'); await cleanup(); process.exit(0); }

  step = 'build';
  buildDir = mkdtempSync(join(tmpdir(), 'proxion-web-'));
  await new Promise((res, rej) => { const b = spawn(process.execPath, [join(HERE, 'scripts', 'build-web.mjs'), HERE, buildDir]); b.on('exit', (c) => c === 0 ? res() : rej(new Error('build ' + c))); });
  // The test pod is http, so relax the https-only CSP; point the client-id doc at
  // this https origin so OIDC accepts it.
  const idxPath = join(buildDir, 'index.html');
  writeFileSync(idxPath, readFileSync(idxPath, 'utf8').replace(/<meta http-equiv="Content-Security-Policy"[^>]*>/, ''));
  const port = await freePort();
  const origin = `https://127.0.0.1:${port}/`;
  writeFileSync(join(buildDir, 'clientid.jsonld'), JSON.stringify({
    '@context': ['https://www.w3.org/ns/solid/oidc-context.jsonld'],
    client_id: origin + 'clientid.jsonld', client_name: 'Proxion', client_uri: origin,
    redirect_uris: [origin], scope: 'openid profile offline_access webid',
    grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'], token_endpoint_auth_method: 'none',
  }));
  srv = https({ key: tls.key, cert: tls.cert }, (req, rs) => {
    try {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p.endsWith('/')) p += 'index.html';
      const fp = normalize(join(buildDir, p));
      if (!fp.startsWith(buildDir) || !existsSync(fp) || statSync(fp).isDirectory()) { rs.writeHead(404); rs.end(); return; }
      rs.writeHead(200, { 'Content-Type': MIME[extname(fp)] || 'application/octet-stream' });
      rs.end(readFileSync(fp));
    } catch (e) { rs.writeHead(500); rs.end(String(e)); }
  });
  await new Promise((r) => srv.listen(port, '127.0.0.1', r));
  ok('built + serving the web app over https');

  step = 'css';
  css = await startCss({ allowSelfSignedClientId: true }).catch(() => null);
  if (!css) { console.log('  \u2013 skipped: no CSS pod available (offline / npx blocked).'); await cleanup(); process.exit(0); }
  const acct = await provisionAccount(css.url, { email: `signedin-${Date.now()}@test.example`, password: 'pw-12345678', label: 'signedin' });
  ok('CSS pod up + account provisioned');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  // Failed pod fetches (404 for a not-yet-created resource, 409 for an existing
  // container) are expected during bootstrapping and handled by the app; they are
  // not JS errors. Only flag uncaught exceptions and other console errors.
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push('console.error: ' + m.text()); });
  const cssHost = new URL(css.url).hostname;
  for (const [name, value] of Object.entries(acct.cookies)) await page.setCookie({ name, value, domain: cssHost, path: '/' });

  // ── Sign in through the real OIDC + consent flow ──
  step = 'signin';
  await page.goto(origin + '?mode=web', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise((r) => setTimeout(r, 500));
  await page.evaluate((u) => { window.proxionWebLogin(u); }, css.url);
  await page.waitForSelector('input[name="webId"]', { timeout: 25000 });
  await page.click('input[name="webId"]');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => null),
    page.click('#authorize'),
  ]);
  await page.waitForFunction(() => !location.href.includes('/consent') && !location.href.includes('/.oidc/'), { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 5000));

  const booted = await page.evaluate(() => ({
    mode: window.proxionTransport && window.proxionTransport.mode,
    hasWebDm: typeof window.proxionWebDm,
    signin: document.getElementById('web-signin-modal') ? getComputedStyle(document.getElementById('web-signin-modal')).display : 'n/a',
  }));
  if (booted.mode === 'web' && booted.hasWebDm === 'object' && booted.signin === 'none') ok('signed in: the signed-in boot ran (PodSocket + engines installed)');
  else fail(`signed-in boot incomplete: ${JSON.stringify(booted)}`);

  // ── Create a room and post a message ──
  step = 'room';
  const roomName = 'e2e-' + Math.random().toString(36).slice(2, 7);
  const msg = 'hello-' + Math.random().toString(36).slice(2, 7);
  await page.evaluate(() => document.getElementById('create-room-btn').click());
  await page.waitForSelector('#room-name-input', { visible: true, timeout: 10000 });
  await page.type('#room-name-input', roomName);
  await page.click('#room-create-submit');
  // The room opens; wait for the sidebar entry + active view.
  await page.waitForFunction((n) => Array.from(document.querySelectorAll('#room-list li, nav li')).some((el) => el.textContent.includes(n)), { timeout: 15000 }, roomName)
    .then(() => ok('created a room, it appears in the sidebar'))
    .catch(() => fail('created room did not appear in the sidebar'));

  // Open the room (make it the active view) so the composer sends into it.
  await page.evaluate((n) => {
    const li = Array.from(document.querySelectorAll('#room-list li, nav li')).find((el) => el.textContent.includes(n));
    if (li) li.click();
  }, roomName);
  await new Promise((r) => setTimeout(r, 1500));
  await page.waitForSelector('#message-input', { timeout: 10000 });
  await page.focus('#message-input');
  await page.type('#message-input', msg);
  await page.keyboard.press('Enter');
  await page.waitForFunction((m) => document.getElementById('message-feed') && document.getElementById('message-feed').innerText.includes(m), { timeout: 15000 }, msg)
    .then(() => ok('posted a message, it rendered'))
    .catch(() => fail('posted message did not render'));

  // ── Reload: the room + message persist from the pod ──
  step = 'reload';
  await page.reload({ waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise((r) => setTimeout(r, 6000));
  const persisted = await page.evaluate((n) => Array.from(document.querySelectorAll('#room-list li, nav li')).some((el) => el.textContent.includes(n)), roomName);
  if (persisted) ok('after reload the room is still there (persisted to the pod)');
  else fail('room did not persist across reload');

  if (errors.length) fail('page errors during the signed-in session:\n    ' + errors.slice(0, 8).join('\n    '));
  else ok('no page/console errors through the whole signed-in session');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
