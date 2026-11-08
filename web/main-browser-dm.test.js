import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { isDmPeerWebId } from './webdm.js';
const source = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
const tail = source.slice(source.indexOf('function _openDirectMessage('));
const declaration = tail.slice(0, /^        }\r?$/m.exec(tail).index + 9);
function client(overrides = {}) {
    const deps = {
        selfWebId: 'https://alice.example/#me', clientDid: 'did:key:self',
        openContactByDid: vi.fn(() => false), isDmPeerWebId, transport: { mode: 'web' }, crossMode: null,
        localDmPeers: {}, _podUpdateDmIndex: vi.fn(async () => {}), window: { proxionWebPresence: { subscribeContact: vi.fn() } },
        _adoptWebProfileName: vi.fn(), _addCrossModeContact: vi.fn(), renderDmSidebar: vi.fn(),
        openLocalDmThread: vi.fn(), dmFallbackName: () => 'Bob', ...overrides,
        showToast: vi.fn(), t: key => key,
    };
    const open = new Function(...Object.keys(deps), `${declaration}; return _openDirectMessage;`)(...Object.values(deps));
    return { open, deps };
}
const BOB = 'https://bob.example/profile/card#me';
describe('DM entry from a room member', () => {
    it('saves a browser contact and opens the WebID thread directly', () => {
        const h = client();
        expect(h.open(BOB)).toBe(true);
        expect(h.deps.localDmPeers[BOB]).toMatchObject({ peer_webid: BOB });
        expect(h.deps._podUpdateDmIndex).toHaveBeenCalledWith(BOB, true);
        expect(h.deps.openLocalDmThread).toHaveBeenCalledWith(BOB, 'Bob', BOB);
    });
    it('reuses a certified contact instead of creating a second thread', () => {
        const h = client({ transport: { mode: 'gateway' }, openContactByDid: vi.fn(() => true) });
        expect(h.open(BOB)).toBe(true);
        expect(h.deps.openLocalDmThread).not.toHaveBeenCalled();
        expect(h.deps._podUpdateDmIndex).not.toHaveBeenCalled();
    });
    it('uses the browser DM path even when pod sync also restored an old gateway contact', () => {
        const h = client({ openContactByDid: vi.fn(() => true) });
        expect(h.open(BOB)).toBe(true);
        expect(h.deps.openContactByDid).not.toHaveBeenCalled();
        expect(h.deps.openLocalDmThread).toHaveBeenCalledWith(BOB, 'Bob', BOB);
    });
    it('does not create self chats or route gateway identities into pod DMs', () => {
        const h = client();
        expect(h.open(h.deps.selfWebId)).toBe(true);
        expect(h.open('did:key:gateway-peer')).toBe(true);
        expect(h.deps.showToast).toHaveBeenCalledWith('contact.memberNeedsWebId');
        expect(h.deps.openLocalDmThread).not.toHaveBeenCalled();
    });
});

it('resumes room requests and room watches when the browser session recovers', () => {
    const body = source.slice(source.indexOf('function _webResume('));
    const fn = body.slice(0, /^        }\r?$/m.exec(body).index + 9);
    const deps = { flushPodQueue: vi.fn(), sendStatus: { retryUndelivered: vi.fn() },
        solidSession: { info: { isLoggedIn: true } }, window: { proxionWebJoin: { resume: vi.fn(async () => {}) } },
        _remoteRooms: { joined: { container: 'https://bob.example/room/' } }, roomCreatorOf: new Set(['owned']),
        _trackRoomSync: vi.fn(), webRoomAccess: { track: vi.fn(), checkAll: vi.fn(async () => {}) } };
    const resume = new Function(...Object.keys(deps), `${fn}; return _webResume;`)(...Object.values(deps));
    resume();
    expect(deps.window.proxionWebJoin.resume).toHaveBeenCalledOnce();
    expect(deps._trackRoomSync.mock.calls).toEqual([['joined'], ['owned']]);
    expect(deps.webRoomAccess.checkAll).toHaveBeenCalledOnce();
});

it('does not restore a waiting banner when approval beat the request acknowledgement', () => {
    const body = source.slice(source.indexOf('function _showJoinPendingBanner('));
    const fn = body.slice(0, /^        }\r?$/m.exec(body).index + 9);
    const clear = vi.fn();
    const show = new Function('_remoteRooms', 'crossRooms', '_clearJoinPendingBanner', `${fn}; return _showJoinPendingBanner;`)(
        { recovered: { owner: 'https://owner.example/#me' } }, null, clear);
    show({ room_id: 'recovered', owner: 'https://owner.example/#me' });
    expect(clear).toHaveBeenCalledWith('recovered');
});
