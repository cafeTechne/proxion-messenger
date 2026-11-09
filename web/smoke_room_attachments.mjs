// Real CSS transfer permissions and the full room attachment size limit.
import { startCss, provisionAccount, makeAuthSession } from './scripts/css-harness.mjs';
import { createRoomAttachments, ROOM_ASSET_MAX } from './room-attachments.js';
import { skipSmoke } from './scripts/smoke-status.mjs';
const css = await startCss().catch(() => null);
if (!css) skipSmoke('No CSS pod available for attachment permission checks.');
try {
    const stamp = Date.now();
    const owner = await provisionAccount(css.url, { email: `asset-owner-${stamp}@test.example`, password: 'pw-12345678', label: 'asset-owner' });
    const peer = await provisionAccount(css.url, { email: `asset-peer-${stamp}@test.example`, password: 'pw-12345678', label: 'asset-peer' });
    const a = await makeAuthSession(owner), b = await makeAuthSession(peer), container = owner.storageRoot + 'proxion/rooms/assets/';
    if (!(await a.fetch(container, { method: 'PUT', headers: { 'Content-Type': 'text/turtle', Link: '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"' }, body: '' })).ok) throw Error('create container');
    const acl = shared => `@prefix acl: <http://www.w3.org/ns/auth/acl#>. <#o> a acl:Authorization; acl:agent <${owner.webId}>; acl:accessTo <${container}>; acl:default <${container}>; acl:mode acl:Read,acl:Write,acl:Control. ${shared ? `<#p> a acl:Authorization; acl:agent <${peer.webId}>; acl:accessTo <${container}>; acl:default <${container}>; acl:mode acl:Read,acl:Append.` : ''}`;
    const grant = async shared => { if (!(await a.fetch(container + '.acl', { method: 'PUT', headers: { 'Content-Type': 'text/turtle' }, body: acl(shared) })).ok) throw Error('ACL update'); };
    await grant(true);
    const sender = createRoomAttachments({ fetch: (url, options) => b.fetch(url, options) }), reader = createRoomAttachments({ fetch: (url, options) => a.fetch(url, options) });
    const bytes = new Uint8Array(ROOM_ASSET_MAX).map((_, index) => index % 251);
    const uploaded = await sender.upload(container, new Blob([bytes]), { id: 'max-asset', name: 'full.txt', mime: 'text/plain' });
    if (!uploaded.ok || uploaded.manifest.chunks.length !== 64) throw Error('2 MiB upload failed: ' + JSON.stringify(uploaded));
    const loaded = await reader.download(container, uploaded.content);
    if (!loaded.ok || loaded.bytes.some((value, index) => value !== bytes[index])) throw Error('download mismatch');
    const chunk = uploaded.manifest.chunks[0];
    if ((await b.fetch(chunk, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status !== 403) throw Error('Member overwrote immutable chunk');
    await grant(false);
    if ((await b.fetch(chunk)).status !== 403) throw Error('Removed member retained file read access');
    if ((await b.fetch(container, { method: 'POST', headers: { 'Content-Type': 'application/json', Slug: 'asset-denied-0.json' }, body: '{}' })).status !== 403) throw Error('Removed member retained upload access');
    if ((await sender.download(container, uploaded.content)).ok) throw Error('Removed member downloaded bytes');
    if (!(await reader.download(container, uploaded.content)).ok) throw Error('Owner lost retained file');
    console.log('SMOKE PASSED: 2 MiB transfer, 64 chunks, exact bytes, immutable member uploads, revoked reads/writes, retained owner access');
} finally { css.stop(); }
