/**
 * PLAN_ROUND_70 Track D — Type Index discoverability against a LIVE pod.
 *
 * Unit tests prove the RDF is well-formed. Only a real server tells us the profile
 * link is accepted and that a registration reads back, i.e. that another Solid app
 * could actually DISCOVER a Proxion chat. Post-conditions:
 *   - a public type index is created AND linked from the WebID card,
 *   - a chat container registers and lists back for meeting:LongChat,
 *   - deregistration removes it.
 *
 * Skipped without a live pod (TEST_CSS_CLIENT_ID). See podcanonical-prototype.js.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';

let _session = null;
let _storageRoot = null;

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import {
    podEnsurePublicTypeIndex, podReadPublicTypeIndexUrl,
    podRegisterRoomChat, podDeregisterRoomChat, podListRegisteredChats,
    ensureProxionContainer,
} from './pod.js';
import { chatRootUrl } from './longchat.js';

const LIVE = !!process.env.TEST_CSS_CLIENT_ID;
const ROOM = `ti-${Math.random().toString(36).slice(2, 10)}`;

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
    try { await podDeregisterRoomChat(ROOM); } catch { /* ignore */ }
    try { await _session.fetch(`${_storageRoot}settings/publicTypeIndex.ttl`, { method: 'DELETE' }); } catch { /* ignore */ }
    await _session.logout();
}, 60000);

describe.skipIf(!LIVE)('Type Index makes a chat discoverable on a live pod', () => {
    it('creates a public type index and links it from the WebID card', async () => {
        const url = await podEnsurePublicTypeIndex();
        expect(url).toBeTruthy();
        // THE discovery gate: another app reads the profile and finds the index.
        const linked = await podReadPublicTypeIndexUrl();
        expect(linked).toBe(url);
    }, 60000);

    it('registers a room chat and lists it back for meeting:LongChat', async () => {
        expect(await podRegisterRoomChat(ROOM)).toBe(true);
        const container = chatRootUrl(_storageRoot, ROOM);
        const listed = await podListRegisteredChats();
        expect(listed).toContain(container);
    }, 60000);

    it('deregisters the chat, removing it from discovery', async () => {
        expect(await podDeregisterRoomChat(ROOM)).toBe(true);
        const container = chatRootUrl(_storageRoot, ROOM);
        const listed = await podListRegisteredChats();
        expect(listed).not.toContain(container);
    }, 60000);
});
