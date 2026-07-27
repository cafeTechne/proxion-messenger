/**
 * PLAN_ROUND_71 B1 — the canonical room descriptor against a LIVE pod. Verifies it
 * writes to rooms/{id}/room.json and reads back parsed, with the Long Chat pointer
 * filled in and membership updates preserved. This is the durable record B2 will
 * rehydrate a room from.
 *
 * Skipped without a live pod (TEST_CSS_CLIENT_ID).
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';

let _session = null;
let _storageRoot = null;

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import { podWriteRoomDescriptor, podReadRoomDescriptor, ensureProxionContainer } from './pod.js';
import { buildRoomDescriptor, withMembers } from './roomdesc.js';
import { chatRootUrl } from './longchat.js';

const LIVE = !!process.env.TEST_CSS_CLIENT_ID;
const ROOM = `rd-${Math.random().toString(36).slice(2, 10)}`;
const webId = () => process.env.TEST_WEBID || (_session && _session.info && _session.info.webId) || '';
const BOB = 'https://bob.example/profile/card#me';

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
}, 90000);

afterAll(async () => {
    if (!LIVE || !_session) return;
    try { await _session.fetch(`${_storageRoot}rooms/${ROOM}/room.json`, { method: 'DELETE' }); } catch { /* ignore */ }
    try { await _session.fetch(`${_storageRoot}rooms/${ROOM}/`, { method: 'DELETE' }); } catch { /* ignore */ }
    await _session.logout();
}, 60000);

describe.skipIf(!LIVE)('room descriptor round-trips on a live pod', () => {
    it('writes and reads back the descriptor with the Long Chat pointer filled in', async () => {
        const desc = buildRoomDescriptor({
            roomId: ROOM, title: 'Interop Room', owner: webId(),
            members: [{ webid: webId(), role: 'owner' }],
        });
        expect(await podWriteRoomDescriptor(desc)).toBe(true);

        const read = await podReadRoomDescriptor(ROOM);
        expect(read).toBeTruthy();
        expect(read.room_id).toBe(ROOM);
        expect(read.owner).toBe(webId());
        expect(read.title).toBe('Interop Room');
        // The writer fills long_chat from the room id when omitted.
        expect(read.long_chat).toBe(chatRootUrl(_storageRoot, ROOM));
        expect(read.members.map(m => m.webid)).toEqual([webId()]);
    }, 60000);

    it('keeps membership current across an update', async () => {
        const base = await podReadRoomDescriptor(ROOM);
        expect(await podWriteRoomDescriptor(withMembers(base, [
            { webid: webId(), role: 'owner' }, { webid: BOB, role: 'member' },
        ]))).toBe(true);

        const read = await podReadRoomDescriptor(ROOM);
        expect(read.members.map(m => m.webid).sort()).toEqual([webId(), BOB].sort());
        expect(read.members.find(m => m.webid === webId()).role).toBe('owner');
        expect(read.members.find(m => m.webid === BOB).role).toBe('member');
    }, 60000);
});
