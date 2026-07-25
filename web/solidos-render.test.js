/**
 * Does the real SolidOS databrowser RENDER a Proxion room as a chat?
 *
 * This is the last gate in PLAN_ROUND_67. The live-pod tests prove a Solid
 * server stores and returns our Long Chat correctly; they do not prove the app
 * everyone in the Solid ecosystem actually uses will show it as a conversation.
 *
 * How it works:
 *   1. write a room through the REAL pod.js Long Chat path
 *   2. make that room world-readable, so the databrowser can read it without an
 *      interactive OIDC login (which is what makes this automatable at all)
 *   3. serve the SolidOS databrowser bundle (mashlib) from a local static server
 *   4. drive it with the same Chrome the other smokes use, at
 *      browse.html?uri=<channel>, and look for our message text on the page
 *
 * Requires a live pod (TEST_CSS_CLIENT_ID) AND the mashlib bundle:
 *   npm install mashlib   (path via MASHLIB_DIST, else auto-detected)
 * Skipped, not silently passed, when either is missing.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { join, extname, resolve } from 'node:path';

let _session = null;
let _storageRoot = null;

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import { podWriteLongChatMessage, ensureProxionContainer } from './pod.js';
import { chatChannelIri, chatRootUrl, dayPath } from './longchat.js';

// TODAY, deliberately: the Long Chat pane opens the current UTC day's file, so
// messages dated in the past would leave it looking at an empty (or absent) day.
const TS = new Date().toISOString();
const ROOM = `solidos-${Math.random().toString(36).slice(2, 8)}`;
const MSG_ONE = 'Hello from Proxion';
const MSG_TWO = 'second line in the room';

const CHROME = [
    process.env.PROXION_CHROME,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find(p => { try { return existsSync(p); } catch { return false; } });

// resolve() so the separator style matches what join() produces below; on
// Windows a forward-slash env value would otherwise fail the containment check
// and every asset 404s.
const MASHLIB = [
    process.env.MASHLIB_DIST,
    'node_modules/mashlib/dist',
    '../node_modules/mashlib/dist',
].filter(Boolean).map(p => resolve(p))
    .find(p => { try { return existsSync(join(p, 'browse.html')); } catch { return false; } });

const LIVE = !!process.env.TEST_CSS_CLIENT_ID && !!CHROME && !!MASHLIB;

const MIME = {
    '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
    '.map': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon', '.json': 'application/json',
};

let server = null;
let browser = null;
let origin = '';

beforeAll(async () => {
    if (!LIVE) return;

    const { Session } = await import('@inrupt/solid-client-authn-node');
    _session = new Session();
    await _session.login({
        clientId: process.env.TEST_CSS_CLIENT_ID,
        clientSecret: process.env.TEST_CSS_CLIENT_SECRET,
        oidcIssuer: process.env.TEST_CSS_ISSUER,
    });
    _storageRoot = process.env.TEST_STORAGE_ROOT;
    await ensureProxionContainer();

    // 1. Write the room through the real Proxion path.
    const me = process.env.TEST_WEBID || _session.info.webId;
    for (const [id, content] of [['m-one', MSG_ONE], ['m-two', MSG_TWO]]) {
        const ok = await podWriteLongChatMessage(ROOM, id, {
            content, from_webid: me, timestamp: TS, room_name: 'Proxion interop room',
        });
        if (!ok) throw new Error(`failed to write ${id} to the pod`);
    }

    // 2. World-readable, so the databrowser needs no interactive login.
    const container = chatRootUrl(_storageRoot, ROOM);
    const acl = `@prefix acl: <http://www.w3.org/ns/auth/acl#>.
@prefix foaf: <http://xmlns.com/foaf/0.1/>.
<#owner> a acl:Authorization;
    acl:agent <${me}>;
    acl:accessTo <${container}>;
    acl:default <${container}>;
    acl:mode acl:Read, acl:Write, acl:Control.
<#public> a acl:Authorization;
    acl:agentClass foaf:Agent;
    acl:accessTo <${container}>;
    acl:default <${container}>;
    acl:mode acl:Read.
`;
    const aclRes = await _session.fetch(container + '.acl', {
        method: 'PUT', headers: { 'Content-Type': 'text/turtle' }, body: acl,
    });
    if (!aclRes.ok) throw new Error(`ACL write failed: ${aclRes.status}`);

    // 3. Serve the databrowser bundle.
    server = createServer((req, res) => {
        const rel = decodeURIComponent((req.url || '/').split('?')[0]).replace(/^\/+/, '') || 'browse.html';
        const file = join(MASHLIB, rel);
        if (!file.startsWith(MASHLIB) || !existsSync(file)) { res.writeHead(404); return res.end('nf'); }
        res.writeHead(200, { 'Content-Type': MIME[extname(file)] || 'application/octet-stream' });
        res.end(readFileSync(file));
    });
    await new Promise(r => server.listen(0, '127.0.0.1', r));
    origin = `http://127.0.0.1:${server.address().port}`;

    // 4. Browser.
    const puppeteer = (await import('puppeteer-core')).default;
    browser = await puppeteer.launch({
        executablePath: CHROME,
        headless: 'new',
        args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });
}, 180000);

afterAll(async () => {
    if (browser) await browser.close().catch(() => {});
    if (server) await new Promise(r => server.close(r));
    if (_session) {
        // Best effort cleanup of the room we created.
        const base = chatRootUrl(_storageRoot, ROOM);
        const d = dayPath(TS);                     // e.g. 2026/07/24
        const [yy, mm] = d.split('/');
        for (const u of [
            `${base}${d}/chat.ttl`, `${base}${d}/`, `${base}${yy}/${mm}/`,
            `${base}${yy}/`, `${base}index.ttl`, `${base}.acl`, base,
        ]) {
            try { await _session.fetch(u, { method: 'DELETE' }); } catch { /* ignore */ }
        }
        await _session.logout();
    }
}, 120000);

describe.skipIf(!LIVE)('SolidOS databrowser renders a Proxion room', () => {
    it('shows the room as a chat with our messages in it', async () => {
        const channel = chatChannelIri(_storageRoot, ROOM);
        const page = await browser.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(String(e.message || e)));

        await page.goto(`${origin}/browse.html?uri=${encodeURIComponent(channel)}`, {
            waitUntil: 'networkidle2', timeout: 90000,
        });

        // The databrowser fetches and renders asynchronously; poll for our text
        // rather than guessing at its internal DOM structure.
        let text = '';
        const deadline = Date.now() + 60000;
        while (Date.now() < deadline) {
            text = await page.evaluate(() => (document.body ? document.body.innerText : ''));
            if (text.includes(MSG_ONE) && text.includes(MSG_TWO)) break;
            await new Promise(r => setTimeout(r, 1000));
        }

        if (!text.includes(MSG_ONE)) {
            console.log('--- databrowser page text (first 1500 chars) ---');
            console.log(text.slice(0, 1500));
            if (errors.length) console.log('--- page errors ---\n' + errors.slice(0, 5).join('\n'));
        }

        expect(text).toContain(MSG_ONE);
        expect(text).toContain(MSG_TWO);
        await page.close();
    }, 180000);
});
