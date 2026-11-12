// Real owner/member/anonymous permission checks; no browser is required.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { startCss, provisionAccount, makeAuthSession } from './scripts/css-harness.mjs';
import { createPodIo } from './podio.js';
import { createPodRoomIo } from './podroomio.js';
import { accessControlUrl, detectAclModel } from './acl.js';

const repo = fileURLToPath(new URL('..', import.meta.url));
const css = await startCss();
try {
    const stamp = Date.now(), provision = label => provisionAccount(css.url, { email: `${label}-${stamp}@test.example`, password: 'pw-12345678', label });
    const accounts = await Promise.all(['owner', 'bob', 'carol'].map(provision));
    const [owner, bob, carol] = accounts, [a, b, c] = await Promise.all(accounts.map(makeAuthSession));
    const io = createPodIo({ fetch: a.fetch, isLoggedIn: () => true, storageRoot: () => owner.storageRoot, webId: () => owner.webId });
    const denied = response => assert([401, 403].includes(response.status), `Unexpected access: ${response.status}`);
    for (const [ensure, path] of [['podEnsureDmInbox', 'dm'], ['podEnsureCallInbox', 'call'], ['podEnsureJoinInbox', 'join'], ['podEnsureFriendInbox', 'friend']]) {
        const inbox = await io[ensure](); assert(inbox, path + ' inbox unavailable');
        const head = await a.fetch(inbox, { method: 'HEAD' }), link = head.headers.get('link');
        assert.equal(detectAclModel(link, accessControlUrl(link, inbox)), css.accessControl);
        const drop = await fetch(inbox, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"test":"drop"}' });
        assert(drop.ok, path + ' append failed: ' + drop.status);
        const url = new URL(drop.headers.get('location'), inbox).href;
        for (const reader of [fetch, b.fetch]) {
            denied(await reader(inbox)); denied(await reader(url));
            denied(await reader(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' }));
            denied(await reader(url, { method: 'DELETE' }));
        }
        assert((await a.fetch(url)).ok); assert((await a.fetch(url, { method: 'DELETE' })).ok);
    }
    console.log(`✓ ${css.accessControl}: four inboxes accept drops while protecting listing, contents, replacement and deletion`);
    assert(await io.podWritePresence('online'));
    const presence = owner.storageRoot + 'proxion/presence.json';
    assert.equal((await (await fetch(presence)).json()).status, 'online');
    denied(await b.fetch(presence, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' }));
    assert(await io.podWritePresence('away'));
    assert.equal((await (await fetch(presence)).json()).status, 'away');
    console.log(`✓ ${css.accessControl}: public presence reads, retained owner updates and denied peer writes`);

    const roomIo = createPodRoomIo({ fetch: a.fetch, isLoggedIn: () => true, storageRoot: () => owner.storageRoot, webId: () => owner.webId });
    const room = owner.storageRoot + 'proxion/rooms/access/';
    const create = async url => assert((await a.fetch(url, { method: 'PUT', headers: { 'Content-Type': 'text/turtle', Link: '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"' }, body: '' })).ok);
    await create(room);
    const grant = members => roomIo.podGrantChatParticipants(room, owner.webId, members);
    assert(await grant([bob.webId, carol.webId]));
    for (const session of [b, c]) assert((await session.fetch(room)).ok);
    const append = await b.fetch(room, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"room":true}' }); assert(append.ok);
    const child = new URL(append.headers.get('location'), room).href;
    assert((await c.fetch(child)).ok); denied(await c.fetch(child, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' }));
    denied(await c.fetch(child, { method: 'DELETE' })); denied(await fetch(child));
    assert(await grant([carol.webId])); denied(await b.fetch(child)); assert((await c.fetch(child)).ok);
    assert(await grant([])); denied(await c.fetch(child)); assert((await a.fetch(child)).ok);
    console.log(`✓ ${css.accessControl}: shared children support both members, append-only access, removal and owner-only closure`);

    if (css.accessControl === 'acp') {
        const container = owner.storageRoot + 'proxion/rooms/python-acp/'; await create(container);
        assert((await a.fetch(container + 'retained.txt', { method: 'PUT', headers: { 'Content-Type': 'text/plain' }, body: 'retained' })).ok);
        const write = (members, encoding, readOnly = false) => new Promise((resolvePromise, reject) => {
            const child = spawn('python', ['scripts/acp_live_policy.py'], { cwd: repo, env: { ...process.env, PYTHONPATH: join(repo, 'proxion-messenger-core/src') } });
            let output = '', errors = '';
            child.stdout.on('data', data => { output += data; }); child.stderr.on('data', data => { errors += data; });
            child.on('error', reject);
            child.on('close', code => code === 0 ? resolvePromise(JSON.parse(output)) : reject(Error('Python ACP writer failed: ' + errors.slice(-1600))));
            child.stdin.end(JSON.stringify({ account: owner, css: css.url, members, encoding, readOnly }));
        });
        for (const encoding of ['json', 'turtle']) {
            await write([bob.webId, carol.webId], encoding);
            for (const session of [b, c]) assert((await session.fetch(container + 'retained.txt')).ok, encoding + ' child inheritance failed');
            await write([carol.webId], encoding); denied(await b.fetch(container + 'retained.txt')); assert((await c.fetch(container + 'retained.txt')).ok);
            await write([], encoding); denied(await c.fetch(container + 'retained.txt')); assert((await a.fetch(container + 'retained.txt')).ok);
        }
        await write([bob.webId], 'json', true); assert((await b.fetch(container + 'retained.txt')).ok);
        denied(await b.fetch(container, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }));
        console.log('✓ Python DPoP HEAD, resolved stash identity, JSON-LD/Turtle multi-member policies, inherited children, revocation and read-only grants');
    }
    console.log('SMOKE PASSED');
} finally { css.stop(); }
