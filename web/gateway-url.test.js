// Gateway HTTP calls must go through gatewayUrl(): in the desktop app the page
// comes from the Tauri asset origin, where a bare '/connectivity' would hit the
// bundled files instead of the sidecar.
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gatewayHttpBase, gatewayUrl, isTauriAssetOrigin, DESKTOP_GATEWAY_HTTP } from './util.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const loc = (href) => new URL(href);

describe('gatewayHttpBase', () => {
  it('is same-origin when the gateway served the page', () => {
    expect(gatewayHttpBase(loc('http://127.0.0.1:8080/'))).toBe('');
    expect(gatewayHttpBase(loc('https://my-gw.example/'))).toBe('');
    expect(gatewayHttpBase(loc('http://localhost:9000/'))).toBe('');
  });
  it('points the desktop webview at the sidecar', () => {
    expect(isTauriAssetOrigin(loc('tauri://localhost/'))).toBe(true);
    expect(isTauriAssetOrigin(loc('https://tauri.localhost/'))).toBe(true);
    expect(gatewayHttpBase(loc('tauri://localhost/'))).toBe(DESKTOP_GATEWAY_HTTP);
    // Windows WebView2 serves the app over https, so https alone is not "served by the gateway".
    expect(gatewayHttpBase(loc('https://tauri.localhost/index.html'))).toBe(DESKTOP_GATEWAY_HTTP);
    expect(gatewayUrl('/health', loc('https://tauri.localhost/'))).toBe('http://127.0.0.1:8080/health');
  });
  it('the desktop port matches what main.rs gives the sidecar', () => {
    const rs = readFileSync(join(HERE, '..', 'tauri-app', 'src-tauri', 'src', 'main.rs'), 'utf8');
    const m = rs.match(/"PROXION_HTTP_PORT"\.into\(\),\s*"(\d+)"/);
    expect(m).toBeTruthy();
    expect(DESKTOP_GATEWAY_HTTP).toBe(`http://127.0.0.1:${m[1]}`);
  });
});

describe('no bare relative gateway fetch', () => {
  const sources = readdirSync(HERE)
    .filter((f) => f.endsWith('.js') && !f.endsWith('.test.js') && !f.includes('bundle') && f !== 'sw.js');

  it('every fetch of a gateway path goes through gatewayUrl()', () => {
    const offenders = [];
    for (const f of sources) {
      const src = readFileSync(join(HERE, f), 'utf8');
      // fetch('/x'), fetch("/x"), fetch(`/x`), and fetch(someBase + '/x').
      const re = /fetch\(\s*(?:['"`]\/|[\w$.]+\s*\+\s*['"`]\/)/g;
      for (const m of src.matchAll(re)) {
        const line = src.slice(0, m.index).split('\n').length;
        offenders.push(`${f}:${line}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('no module hardcodes the desktop gateway address', () => {
    const offenders = sources.filter((f) => f !== 'util.js'
      && readFileSync(join(HERE, f), 'utf8').includes('127.0.0.1:8080'));
    expect(offenders).toEqual([]);
  });
});
