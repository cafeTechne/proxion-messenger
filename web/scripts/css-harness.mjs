// css-harness.mjs — spin up a throwaway Community Solid Server and provision an
// account, so the web build's live-pod tests run with zero external setup.
//
// This is what automates the previously-manual "needs a provisioned CSS pod"
// gate: startCss() launches CSS via npx on a free port, and provisionAccount()
// drives the CSS v7 account API (the same flow as the Python CssAccountManager)
// to mint client credentials + a pod. Together they yield everything
// @inrupt/solid-client-authn-node needs for an authenticated session.
//
// Used by pod-nogw.integration.test.js. Requires network access on first run
// (npx downloads @solid/community-server); self-skips upstream if unavailable.

import { spawn } from 'node:child_process';
import { createServer, connect as netConnect } from 'node:net';
import { randomUUID, createHash } from 'node:crypto';
import { SignJWT, exportJWK, generateKeyPair } from 'jose';

export function freePort() {
    return new Promise((res, rej) => {
        const s = createServer();
        s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
        s.on('error', rej);
    });
}

function waitForPort(port, timeoutMs) {
    return new Promise((res, rej) => {
        const deadline = Date.now() + timeoutMs;
        const tryOnce = () => {
            const c = netConnect(port, '127.0.0.1');
            c.once('connect', () => { c.destroy(); res(); });
            c.once('error', () => { c.destroy(); Date.now() > deadline ? rej(new Error(`port ${port} idle`)) : setTimeout(tryOnce, 300); });
        };
        tryOnce();
    });
}

// Start CSS (in-memory backend) on a free port. Returns { url, port, stop() } or
// throws/returns null if CSS cannot be launched (offline, npx blocked).
export async function startCss({ readyTimeoutMs = 120000 } = {}) {
    const port = await freePort();
    // Sanitize the child env. Under vitest, NODE_ENV=test makes a CSS dependency
    // reference a `jest` global (500s with "jest is not defined"), and the vitest
    // loader/worker vars must not leak into the CSS process either.
    const env = { ...process.env, NODE_ENV: 'production' };
    delete env.NODE_OPTIONS;
    delete env.VITEST;
    delete env.VITEST_MODE;
    delete env.VITEST_POOL_ID;
    delete env.VITEST_WORKER_ID;
    const proc = spawn(
        'npx', ['-y', '@solid/community-server', '-p', String(port), '-l', 'warn'],
        { shell: process.platform === 'win32', env },
    );
    let log = '';
    proc.stdout.on('data', (d) => { log += d; });
    proc.stderr.on('data', (d) => { log += d; });
    const deadline = Date.now() + readyTimeoutMs;
    while (Date.now() < deadline) {
        if (/listening|started|Running at/i.test(log)) break;
        if (proc.exitCode !== null) throw new Error('CSS exited early:\n' + log.slice(-800));
        await new Promise((r) => setTimeout(r, 500));
    }
    await waitForPort(port, 15000);
    return {
        url: `http://localhost:${port}`,
        port,
        stop() { try { proc.kill(); } catch { /* already gone */ } },
        _log: () => log,
    };
}

// Minimal cookie jar over fetch — CSS's account API is cookie-session based.
class Jar {
    constructor() { this.cookies = {}; }
    _cookieHeader() { return Object.entries(this.cookies).map(([k, v]) => `${k}=${v}`).join('; '); }
    _store(res) {
        const setCookies = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
        for (const c of setCookies) {
            const kv = c.split(';')[0];
            const i = kv.indexOf('=');
            if (i > 0) this.cookies[kv.slice(0, i).trim()] = kv.slice(i + 1).trim();
        }
    }
    async fetch(url, { json, headers = {}, ...opts } = {}) {
        const h = { Accept: 'application/json', ...headers };
        if (this._cookieHeader()) h.Cookie = this._cookieHeader();
        if (json !== undefined) { h['Content-Type'] = 'application/json'; opts.body = JSON.stringify(json); }
        const res = await fetch(url, { ...opts, headers: h });
        this._store(res);
        return res;
    }
}

// ── Authenticated session via DPoP client credentials ────────────────────────
//
// @inrupt/solid-client-authn-node uses openid-client, which does not work under
// vitest's module environment. So the harness mints a DPoP-bound access token by
// hand (global fetch + jose) and returns a { info, fetch } shaped like the pod.js
// solidSession, ready to drop into the vi.mock('./auth.js'). This runs identically
// in plain node and vitest.

const b64url = (buf) => Buffer.from(buf).toString('base64url');

async function _dpopProof(privateKey, jwk, htm, htu, { nonce, ath } = {}) {
    const payload = { htm, htu, jti: randomUUID() };
    if (nonce) payload.nonce = nonce;
    if (ath) payload.ath = ath;
    return new SignJWT(payload)
        .setProtectedHeader({ alg: 'ES256', typ: 'dpop+jwt', jwk })
        .setIssuedAt()
        .sign(privateKey);
}

// Build an authenticated session for a provisioned account. Returns
// { info: { isLoggedIn, webId }, fetch } — the shape pod.js expects from
// solidSession (it only uses .info and .fetch).
export async function makeAuthSession(acct) {
    const { publicKey, privateKey } = await generateKeyPair('ES256', { extractable: true });
    const jwk = await exportJWK(publicKey);
    const discovery = await (await fetch(acct.issuer.replace(/\/?$/, '') + '/.well-known/openid-configuration')).json();
    const tokenEndpoint = discovery.token_endpoint;

    const requestToken = async (nonce) => {
        const proof = await _dpopProof(privateKey, jwk, 'POST', tokenEndpoint, { nonce });
        return fetch(tokenEndpoint, {
            method: 'POST',
            headers: {
                Authorization: 'Basic ' + Buffer.from(
                    `${encodeURIComponent(acct.clientId)}:${encodeURIComponent(acct.clientSecret)}`).toString('base64'),
                'Content-Type': 'application/x-www-form-urlencoded',
                DPoP: proof,
            },
            body: 'grant_type=client_credentials&scope=webid',
        });
    };
    let res = await requestToken();
    if (res.status === 400 || res.status === 401) {
        const nonce = res.headers.get('DPoP-Nonce');
        if (nonce) res = await requestToken(nonce);
    }
    if (!res.ok) throw new Error(`token request failed: ${res.status} ${(await res.text()).slice(0, 200)}`);
    const accessToken = (await res.json()).access_token;
    const ath = b64url(createHash('sha256').update(accessToken).digest());

    let rsNonce = null;
    const authFetch = async (url, opts = {}) => {
        const u = new URL(url);
        const htu = u.origin + u.pathname;   // DPoP htu excludes query/fragment
        const method = (opts.method || 'GET').toUpperCase();
        const doFetch = async (nonce) => {
            const proof = await _dpopProof(privateKey, jwk, method, htu, { nonce, ath });
            return fetch(url, { ...opts, headers: { ...(opts.headers || {}), Authorization: 'DPoP ' + accessToken, DPoP: proof } });
        };
        let r = await doFetch(rsNonce);
        if (r.status === 401) {
            const n = r.headers.get('DPoP-Nonce');
            if (n && n !== rsNonce) { rsNonce = n; r = await doFetch(rsNonce); }
        } else {
            const n = r.headers.get('DPoP-Nonce');
            if (n) rsNonce = n;
        }
        return r;
    };
    return { info: { isLoggedIn: true, webId: acct.webId }, fetch: authFetch };
}

async function controlsOf(jar, cssUrl) {
    const res = await jar.fetch(`${cssUrl}/.account/`);
    if (!res.ok) throw new Error(`GET /.account/ -> ${res.status}`);
    return (await res.json()).controls || {};
}

// Register an account, create a pod, and issue client credentials. Returns
// everything an authn-node Session needs: { clientId, clientSecret, issuer,
// podUrl, webId, storageRoot }.
export async function provisionAccount(cssUrl, { email, password, label = 'proxion' }) {
    const jar = new Jar();
    // 1. Create an account session (sets the cookie).
    const acct = await jar.fetch(`${cssUrl}/.account/account/`, { method: 'POST' });
    if (!acct.ok) throw new Error(`create account -> ${acct.status}`);
    // 2. Set the password login.
    let controls = await controlsOf(jar, cssUrl);
    const pwCreate = controls.password && controls.password.create;
    if (!pwCreate) throw new Error('no password.create control');
    const pw = await jar.fetch(pwCreate, { method: 'POST', json: { email, password } });
    if (!pw.ok) throw new Error(`set password -> ${pw.status}`);
    // 3. Re-read controls (now fully authenticated) and create a pod.
    controls = await controlsOf(jar, cssUrl);
    const podRes = await jar.fetch(controls.account.pod, { method: 'POST', json: { name: email.split('@')[0] } });
    if (!podRes.ok) throw new Error(`create pod -> ${podRes.status}`);
    const { pod: podUrl, webId } = await podRes.json();
    // 4. Issue client credentials bound to the WebID.
    const credRes = await jar.fetch(controls.account.clientCredentials, { method: 'POST', json: { name: label, webId } });
    if (!credRes.ok) throw new Error(`issue credentials -> ${credRes.status}`);
    const cred = await credRes.json();
    return {
        clientId: cred.id,
        clientSecret: cred.secret,
        issuer: cssUrl,
        podUrl,
        webId,
        storageRoot: podUrl.replace(/\/?$/, '/'),
    };
}
