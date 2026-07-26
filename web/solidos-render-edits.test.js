/**
 * PLAN_ROUND_68 Phase B, against the REAL SolidOS databrowser.
 *
 * The unit + live tests prove our edit/delete PATCHes are well-formed and that
 * CSS accepts them. They do NOT prove the app the Solid ecosystem actually uses
 * honours them. R67 taught us the written spec is not enough (the databrowser
 * reads wf:message, not the spec's meeting:message), so edits and deletes get the
 * same treatment: drive the databrowser and see what it actually shows.
 *
 *   edit   an in-place sioc:content rewrite  -> the NEW text shows, old is gone
 *   delete a schema:dateDeleted tombstone     -> does the databrowser hide it?
 *
 * Requires a live pod (TEST_CSS_CLIENT_ID), the mashlib bundle and Chrome; it is
 * skipped (not silently passed) when any is missing.
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

import {
    podWriteLongChatMessage, podEditLongChatMessage,
    podSoftDeleteLongChatMessage, ensureProxionContainer,
} from './pod.js';
import { chatChannelIri, chatRootUrl, dayPath } from './longchat.js';

const TS = new Date().toISOString();                 // today: the pane opens today's day file
const ROOM = `solidos-b-${Math.random().toString(36).slice(2, 8)}`;
const ORIGINAL = 'ORIGINAL text before the edit';
const EDITED = 'EDITED text after the change';
const DELETED = 'this message SHOULD be withdrawn';
const KEPT = 'a normal message that stays';

const CHROME = [
    process.env.PROXION_CHROME,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find(p => { try { return existsSync(p); } catch { return false; } });

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

    const me = process.env.TEST_WEBID || _session.info.webId;
    for (const [id, content] of [
        ['m-edit', ORIGINAL], ['m-del', DELETED], ['m-keep', KEPT],
    ]) {
        const ok = await podWriteLongChatMessage(ROOM, id, {
            content, from_webid: me, timestamp: TS, room_name: 'Proxion interop room',
        });
        if (!ok) throw new Error(`failed to write ${id}`);
    }
    // The two Phase B operations, through the real pod path.
    if (!await podEditLongChatMessage(ROOM, 'm-edit', TS, EDITED)) throw new Error('edit PATCH failed');
    if (!await podSoftDeleteLongChatMessage(ROOM, 'm-del', TS)) throw new Error('delete PATCH failed');

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

    server = createServer((req, res) => {
        const rel = decodeURIComponent((req.url || '/').split('?')[0]).replace(/^\/+/, '') || 'browse.html';
        const file = join(MASHLIB, rel);
        if (!file.startsWith(MASHLIB) || !existsSync(file)) { res.writeHead(404); return res.end('nf'); }
        res.writeHead(200, { 'Content-Type': MIME[extname(file)] || 'application/octet-stream' });
        res.end(readFileSync(file));
    });
    await new Promise(r => server.listen(0, '127.0.0.1', r));
    origin = `http://127.0.0.1:${server.address().port}`;

    const puppeteer = (await import('puppeteer-core')).default;
    browser = await puppeteer.launch({
        executablePath: CHROME, headless: 'new',
        args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });
}, 180000);

afterAll(async () => {
    if (browser) await browser.close().catch(() => {});
    if (server) await new Promise(r => server.close(r));
    if (_session) {
        const base = chatRootUrl(_storageRoot, ROOM);
        const d = dayPath(TS);
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

describe.skipIf(!LIVE)('SolidOS databrowser: edits and deletes (Phase B)', () => {
    it('shows the edited text (not the old) and reveals how it treats a tombstone', async () => {
        const channel = chatChannelIri(_storageRoot, ROOM);
        const page = await browser.newPage();
        await page.goto(`${origin}/browse.html?uri=${encodeURIComponent(channel)}`, {
            waitUntil: 'networkidle2', timeout: 90000,
        });

        // Wait until at least the kept + edited messages have rendered.
        let text = '';
        const deadline = Date.now() + 60000;
        while (Date.now() < deadline) {
            text = await page.evaluate(() => (document.body ? document.body.innerText : ''));
            if (text.includes(KEPT) && text.includes(EDITED)) break;
            await new Promise(r => setTimeout(r, 1000));
        }
        await page.close();

        const showsDeleted = text.includes(DELETED);
        console.log(`[phase-b render] edited-shows=${text.includes(EDITED)} ` +
            `old-gone=${!text.includes(ORIGINAL)} kept=${text.includes(KEPT)} ` +
            `tombstone-still-visible=${showsDeleted}`);

        // Edit: an in-place content rewrite is honoured by any reader of
        // sioc:content, so this is a firm assertion.
        expect(text).toContain(EDITED);
        expect(text).not.toContain(ORIGINAL);
        expect(text).toContain(KEPT);

        // Delete: this test was written to answer whether the databrowser hides a
        // schema:dateDeleted tombstone. It DOES (verified: tombstone-still-visible
        // =false), so a soft-delete really withdraws the message cross-app, no
        // content-removal needed. Firm assertion now, so a regression fails loudly.
        expect(showsDeleted).toBe(false);
    }, 180000);
});
