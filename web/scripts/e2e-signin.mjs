// e2e-signin.mjs — shared helpers for the signed-in browser E2E smokes.
//
// Completing a real Solid-OIDC login headlessly needs three things: the app
// served over https (so OIDC accepts the client-id document), CSS trusting that
// self-signed cert (startCss({ allowSelfSignedClientId: true })), and the
// browser holding CSS's account cookie (so the consent screen renders). This
// module packages the cert + build + https server, and the sign-in drive.

import { existsSync, mkdtempSync, readFileSync, writeFileSync, statSync } from 'fs';
import { spawn } from 'child_process';
import { createServer as https } from 'https';
import { createServer as net } from 'net';
import { tmpdir } from 'os';
import { join, extname, normalize } from 'path';

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.json': 'application/json', '.jsonld': 'application/ld+json', '.css': 'text/css',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
};

export function freePort() {
  return new Promise((r, j) => { const s = net(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); s.on('error', j); });
}

// Make a throwaway self-signed cert for 127.0.0.1. Returns { key, cert } or null.
export async function makeCert() {
  const dir = mkdtempSync(join(tmpdir(), 'proxion-cert-'));
  const keyPath = join(dir, 'key.pem'), certPath = join(dir, 'cert.pem');
  const okGen = await new Promise((res) => {
    const p = spawn('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-keyout', keyPath, '-out', certPath,
      '-days', '1', '-nodes', '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'],
      { stdio: 'ignore' });
    p.on('exit', (c) => res(c === 0));
    p.on('error', () => res(false));
  });
  if (!okGen || !existsSync(certPath)) return null;
  return { key: readFileSync(keyPath), cert: readFileSync(certPath), dir };
}

// Build the web app, inject an https-origin client-id doc (and drop the CSP so an
// http test pod is reachable), and serve it over https. Returns
// { origin, server, buildDir } or null if the cert/build fails.
export async function buildAndServeHttps(webDir, buildWebScript) {
  const tls = await makeCert();
  if (!tls) return null;
  const buildDir = mkdtempSync(join(tmpdir(), 'proxion-web-'));
  const built = await new Promise((res) => { const b = spawn(process.execPath, [buildWebScript, webDir, buildDir]); b.on('exit', (c) => res(c === 0)); b.on('error', () => res(false)); });
  if (!built) return null;
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
  const server = https({ key: tls.key, cert: tls.cert }, (req, rs) => {
    try {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p.endsWith('/')) p += 'index.html';
      const fp = normalize(join(buildDir, p));
      if (!fp.startsWith(buildDir) || !existsSync(fp) || statSync(fp).isDirectory()) { rs.writeHead(404); rs.end(); return; }
      rs.writeHead(200, { 'Content-Type': MIME[extname(fp)] || 'application/octet-stream' });
      rs.end(readFileSync(fp));
    } catch (e) { rs.writeHead(500); rs.end(String(e)); }
  });
  await new Promise((r) => server.listen(port, '127.0.0.1', r));
  return { origin, server, buildDir, certDir: tls.dir };
}

// Drive a page from the app through the full OIDC + consent flow to signed-in.
// `cookies` are the account's CSS cookies (from provisionAccount). Resolves once
// the app is back and logged in (PodSocket installed), throws on failure.
export async function signIn(page, { appOrigin, cssUrl, cookies }) {
  const cssHost = new URL(cssUrl).hostname;
  for (const [name, value] of Object.entries(cookies)) await page.setCookie({ name, value, domain: cssHost, path: '/' });
  await page.goto(appOrigin + '?mode=web', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise((r) => setTimeout(r, 500));
  await page.evaluate((u) => { window.proxionWebLogin(u); }, cssUrl);
  await page.waitForSelector('input[name="webId"]', { timeout: 25000 });
  await page.click('input[name="webId"]');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => null),
    page.click('#authorize'),
  ]);
  await page.waitForFunction(() => !location.href.includes('/consent') && !location.href.includes('/.oidc/'), { timeout: 30000 });
  await page.waitForFunction(() => typeof window.proxionWebDm === 'object', { timeout: 20000 });
}
