// Throwaway gateway for the single-gateway browser smokes (browser, webrtc,
// visual). When PROXION_SMOKE_URL is set the smoke uses that gateway instead.
//
//   const gw = await smokeGateway();   // { url, stop }
//   ...
//   await gw.stop();
//
// The gateway runs run_gateway.py (self-signed https, like a real install) on
// free ports with a fresh data dir, so every run starts from an empty account.
import { spawn } from 'child_process';
import { mkdtempSync, rmSync } from 'fs';
import { createServer, connect } from 'net';
import { tmpdir } from 'os';
import { join, resolve } from 'path';
import { fileURLToPath } from 'url';

const REPO = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const freePort = () => new Promise((res, rej) => {
  const s = createServer();
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
  s.on('error', rej);
});
const portUp = (port) => new Promise((res) => {
  const c = connect(port, '127.0.0.1');
  c.once('connect', () => { c.destroy(); res(true); });
  c.once('error', () => { c.destroy(); res(false); });
});

export async function smokeGateway() {
  if (process.env.PROXION_SMOKE_URL) return { url: process.env.PROXION_SMOKE_URL, stop: async () => {} };
  const httpPort = await freePort(), wsPort = await freePort();
  const dataDir = mkdtempSync(join(tmpdir(), 'proxion-smoke-'));
  const python = process.env.PROXION_PYTHON || 'python';
  const gw = spawn(python, ['run_gateway.py'], {
    cwd: REPO,
    env: {
      ...process.env, PROXION_DATA_DIR: dataDir, PROXION_HOST: '127.0.0.1',
      PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
      PROXION_REQUIRE_AUTH: '0', PROXION_PUBLIC_URL: '', PROXION_WEB_DIR: join(REPO, 'web'),
      PROXION_CSS_URL: '', PROXION_CSS_EMAIL: '', PROXION_CSS_PASSWORD: '',
    },
  });
  let log = '';
  gw.stdout.on('data', (d) => { log += d; });
  gw.stderr.on('data', (d) => { log += d; });
  const stop = async () => {
    try { gw.kill(); } catch { /* already gone */ }
    await sleep(300);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch { /* still locked */ }
  };
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    if (gw.exitCode !== null) break;
    if (/PROXION_GATEWAY_READY/.test(log) && await portUp(httpPort)) {
      return { url: `https://127.0.0.1:${httpPort}/`, stop };
    }
    await sleep(300);
  }
  await stop();
  throw new Error(`test gateway did not start:\n${log.slice(-800)}`);
}
