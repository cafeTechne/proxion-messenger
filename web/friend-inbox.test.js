import { expect, it, vi } from 'vitest';
import { createPodIo } from './podio.js';
const root = 'https://owner.example/', inbox = root + 'proxion/friend-inbox/';
function harness(fetch) { return createPodIo({ fetch, storageRoot: () => root, isLoggedIn: () => true, webId: () => root + 'profile/card#me' }); }
it('keeps friend listening uninitialized when public Append provisioning fails, so it can retry', async () => {
    let refuse = true;
    const fetch = vi.fn(async (url, options = {}) => ({ ok: !refuse || !url.endsWith('.acl'), status: refuse && url.endsWith('.acl') ? 503 : 200,
        headers: new Headers(), text: async () => '' }));
    const io = harness(fetch);
    expect(await io.podEnsureFriendInbox()).toBe(null);
    refuse = false; expect(await io.podEnsureFriendInbox()).toBe(inbox);
});
it('never follows forged containment outside the friend inbox or deletes other private data', async () => {
    const foreign = root + 'proxion/friends/index.json', safe = inbox + 'valid.json';
    const fetch = vi.fn(async url => ({ ok: true, status: 200, headers: new Headers(),
        text: async () => JSON.stringify(url === inbox ? { 'http://www.w3.org/ns/ldp#contains': [{ '@id': foreign }, { '@id': safe }, { '@id': inbox + 'bad%2fpath' }] } : { v: 1 }) }));
    const io = harness(fetch);
    expect(await io.podReadFriendDrops()).toEqual([{ url: safe, snapshot: { v: 1 } }]);
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([inbox, safe]);
    expect(await io.podDeleteFriendDrop(foreign)).toBe(false);
    expect(await io.podDeleteFriendDrop(inbox + '../friends/index.json')).toBe(false);
    expect(await io.podDeleteFriendDrop(inbox + '..')).toBe(false);
    expect(await io.podDeleteFriendDrop(safe)).toBe(true);
});
