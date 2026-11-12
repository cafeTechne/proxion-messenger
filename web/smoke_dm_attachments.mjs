// Real CSS storage protocol: max-sized encrypted DM file, retry, anonymous
// ciphertext reads and denied peer writes. This does not exercise chat controls.
import { startCss, provisionAccount, makeAuthSession } from './scripts/css-harness.mjs';
import { createDmAttachments, prepareDmAttachment, parseDmAttachment, dmAssetContainer, DM_ASSET_MAX } from './dm-attachments.js';
import { skipSmoke } from './scripts/smoke-status.mjs';
import { accessControlUrl, detectAclModel } from './acl.js';
const css = await startCss().catch(() => null);
if (!css) skipSmoke('No CSS pod available for encrypted DM asset checks.');
try {
    const stamp = Date.now(), account = label => provisionAccount(css.url, { email: `${label}-${stamp}@test.example`, password: 'pw-12345678', label });
    const owner = await account('dm-asset-owner'), peer = await account('dm-asset-peer');
    const a = await makeAuthSession(owner), b = await makeAuthSession(peer);
    const bytes = new Uint8Array(DM_ASSET_MAX).map((_, index) => index % 251);
    const prepared = await prepareDmAttachment(new Blob([bytes]), { name: 'private-full.txt', mime: 'text/plain' });
    const sender = createDmAttachments({ fetch: async (url, options) => {
        const response = await a.fetch(url, options);
        if (options?.method === 'DELETE' && ![200, 204, 205, 404, 410].includes(response.status)) console.error('cleanup DELETE status:', response.status);
        return response;
    }, getSelfWebId: () => owner.webId });
    let progress = 0;
    const result = await sender.upload(owner.webId, prepared, count => { progress = count; });
    if (!result.ok || progress !== 65) throw Error('2 MiB upload: ' + JSON.stringify(result));
    const container = dmAssetContainer(owner.webId), chunk = container + `asset-${result.manifest.id}-0.json`;
    const head = await a.fetch(container, { method: 'HEAD' }), link = head.headers.get('link');
    if (detectAclModel(link, accessControlUrl(link, container)) !== css.accessControl) throw Error('Unexpected access-control model');
    const anonymous = await fetch(chunk);
    if (!anonymous.ok) throw Error('recipient could not read public ciphertext');
    const publicDoc = await anonymous.json();
    if (Object.keys(publicDoc).sort().join(',') !== 'data,id,index,v') throw Error('plaintext metadata leaked');
    const reader = createDmAttachments({ fetch, getSelfWebId: () => peer.webId });
    const loaded = await reader.download(owner.webId, prepared.content);
    if (!loaded.ok || loaded.bytes.length !== bytes.length || loaded.bytes.some((value, index) => value !== bytes[index])) throw Error('decrypted bytes mismatch');
    // A reloaded sender restores the encrypted prepared draft, not a fresh key.
    const resumed = createDmAttachments({ fetch: (url, options) => a.fetch(url, options) });
    if (!(await resumed.upload(owner.webId, structuredClone(prepared))).ok) throw Error('resumed upload failed');
    if ((await b.fetch(chunk, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status !== 403) throw Error('peer could overwrite ciphertext');
    const deletion = await fetch(chunk, { method: 'DELETE' });
    if (![401, 403].includes(deletion.status)) throw Error('anonymous delete status: ' + deletion.status);
    const before = await a.fetch(chunk).then(response => response.text());
    if ((await a.fetch(chunk, { method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-None-Match': '*' }, body: '{}' })).status !== 412) throw Error('conditional replacement allowed');
    if (await a.fetch(chunk).then(response => response.text()) !== before) throw Error('ciphertext replaced');
    const manifest = parseDmAttachment(prepared.content);
    if ((await reader.download(owner.webId, prepared.content.replace(manifest.key, 'A'.repeat(43)))).ok) throw Error('wrong key decrypted file');
    const voice = await prepareDmAttachment(new Blob(['voice-bytes']), { name: 'voice.webm', mime: 'audio/webm', kind: 'voice', durationMs: 1500 });
    if (!(await sender.upload(owner.webId, voice)).ok || !(await reader.download(owner.webId, voice.content)).ok) throw Error('voice storage roundtrip');
    const removed = await sender.discardUnused(owner.webId, structuredClone(prepared));
    if (!removed.ok) throw Error('conditional cleanup failed: ' + removed.reason);
    if ((await reader.download(owner.webId, prepared.content)).ok) throw Error('cleaned ciphertext remained downloadable');
    if (!(await reader.download(owner.webId, voice.content)).ok) throw Error('cleanup affected another upload');
    console.log('SMOKE PASSED: 2 MiB/65 ciphertext chunks, exact decryption, persisted-preparation retry, no public metadata/key, denied peer writes, conditional cleanup preserving another upload and voice metadata');
} finally { css.stop(); }
