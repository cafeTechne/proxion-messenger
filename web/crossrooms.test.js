// crossrooms.js: desktop pod rooms over the gateway's pod bridge. Joining a
// browser-build user's room, hosting one, and the removal path.
import { describe, it, expect, vi } from 'vitest';
import { createCrossRooms, vetJoinedRoom, webAppUrl, WEB_APP_URL } from './crossrooms.js';
import { createGwPodBridge } from './gwpod.js';
import { inviteJoinCommand } from './rooms.js';
import { parseInvite } from './webjoin.js';
import { joinedCacheKey, LEGACY_JOINED_ROOMS_UNREADABLE } from './webrooms.js';

const ME = 'https://pod.example/me/profile/card#me';
const ROOT = 'https://pod.example/me/';
const BOB = 'https://pods.example/bob/profile/card#me';
const BOB_ROOT = 'https://pods.example/bob/';
const ROOM = 'room-1';
const CONTAINER = BOB_ROOT + `proxion/rooms/${ROOM}/`;

const settle = (ms = 20) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 2000) {
    const end = Date.now() + ms;
    while (Date.now() < end && !fn()) await settle(10);
    return fn();
}

// A fake gateway behind a real gwpod bridge: pod_fetch answered from a route
// table (a function of method + url), pod_room_register from `register`.
function harness({ routes = () => null, register = () => ({ ok: true }), pending = true, joined = [], storage, accessPollMs, setAccessTimer, clearAccessTimer } = {}) {
    const sent = [], events = [], notes = [], rooms = [], removed = [];
    let bridge = null, cr = null;
    let state = { ready: true, started: true, webid: ME, pod_root: ROOT };
    const descriptorVersions = new Map();
    const send = (m) => {
        sent.push(m);
        if (m.cmd === 'pod_fetch') {
            const r = routes(m.method, m.url, m) || ((m.method === 'GET' || m.method === 'HEAD') ? { status: 404 } : { status: 201 });
            if (m.url.endsWith('/room.json') && m.method === 'PUT' && r.status >= 200 && r.status < 300) {
                descriptorVersions.set(m.url, (descriptorVersions.get(m.url) || 0) + 1);
            }
            const headers = m.method === 'GET' && m.url.endsWith('/room.json')
                ? { etag: '"descriptor-' + (descriptorVersions.get(m.url) || 0) + '"' } : {};
            setTimeout(() => bridge.handleEvent({ type: 'pod_fetch_result', req_id: m.req_id, headers, body: '', ...r }), 0);
        } else if (m.cmd === 'pod_room_register') {
            const r = register(m);
            setTimeout(() => cr.handleEvent({ type: 'pod_room_registered', room_id: m.room_id, container: CONTAINER, ...r }), 0);
        }
    };
    bridge = createGwPodBridge({ send });
    bridge.handleEvent({ type: 'pod_bridge', ready: true, webid: ME, pod_root: ROOT, auth: 'dpop', consumer: true });
    // The pod-side reads crossrooms makes through podio, stubbed to the scenario.
    bridge.io.podHasPendingJoin = vi.fn(async () => pending);
    bridge.io.podDeletePendingJoin = vi.fn(async () => {});
    bridge.io.podReadJoinedRooms = vi.fn(async () => joined);
    bridge.io.podWriteJoinedRooms = vi.fn(async () => true);
    bridge.io.podReadJoinedRoomsVersion = vi.fn(async () => ({
        records: await bridge.io.podReadJoinedRooms(), exists: true, etag: '"joined-1"',
        resource: ROOT + 'proxion/joined-rooms.json',
    }));
    bridge.io.podWriteJoinedRoomsVersion = vi.fn(async (list) => ({
        ok: await bridge.io.podWriteJoinedRooms(list), conflict: false,
    }));
    bridge.io.podReadJoins = vi.fn(async () => []);
    bridge.io.podEnsureJoinInbox = vi.fn(async () => ROOT + 'proxion/join-inbox/');
    bridge.io.podDropJoin = vi.fn(async () => true);
    bridge.io.podWritePendingJoin = vi.fn(async () => true);
    bridge.io.podFetchPeerSigner = vi.fn(async () => null);
    cr = createCrossRooms({
        bridge, send,
        handleEvent: (ev) => events.push(ev),
        getState: () => state,
        getSelfId: () => 'did:key:zDevice',
        getDisplayName: () => 'Me',
        getSigner: () => null,
        askApprove: vi.fn(async () => 'yes'),
        onRoom: (rec, info) => rooms.push({ rec, info }),
        onRemoved: (id) => removed.push(id),
        notify: (kind, d) => notes.push({ kind, ...d }),
        inviteBase: () => 'https://web.example/app/',
        timeoutMs: 500,
        accessPollMs, setAccessTimer, clearAccessTimer,
        storage,
    });
    return { cr, bridge, sent, events, notes, rooms, removed, setState: (value) => { state = value; } };
}

const fetches = (h, method) => h.sent.filter((m) => m.cmd === 'pod_fetch' && (!method || m.method === method));

async function joinBobsRoom(h) {
    h.cr.onState({ ready: true, started: true, webid: ME, pod_root: ROOT });
    await settle();
    const appr = { kind: 'join_approved', room_id: ROOM, owner_webid: BOB, owner_pod_root: BOB_ROOT, title: 'Bob room' };
    h.bridge.io.podReadJoins = vi.fn(async () => [{ url: ROOT + 'proxion/join-inbox/a1', msg: appr }]);
    h.bridge.io.podDeleteJoin = vi.fn(async () => true);
    await h.cr.drainJoins();
    await settle();
}

describe('vetJoinedRoom', () => {
    it('derives the container from the approver WebID and refuses anything else', () => {
        expect(vetJoinedRoom(ROOM, BOB, BOB_ROOT, ROOT)).toBe(CONTAINER);
        expect(vetJoinedRoom(ROOM, BOB, 'https://evil.example/bob/', ROOT)).toBe(null);
        expect(vetJoinedRoom(ROOM, BOB, 'http://127.0.0.1:9/', ROOT)).toBe(null);
        expect(vetJoinedRoom('../x', BOB, BOB_ROOT, ROOT)).toBe(null);
    });
});

describe('invites', () => {
    it('a desktop pod room invite opens the public web build, never the loopback gateway', () => {
        expect(webAppUrl(null)).toBe(WEB_APP_URL);
        expect(webAppUrl({ getItem: () => 'http://127.0.0.1:8080/' })).toBe(WEB_APP_URL);
        expect(webAppUrl({ getItem: () => 'https://my.example/app/' })).toBe('https://my.example/app/');
        const h = harness();
        const link = h.cr.inviteFor(ROOM);
        expect(link.startsWith('https://web.example/app/?join=')).toBe(true);
        expect(link).toContain('mode=web');
        expect(parseInvite(link)).toEqual({ roomId: ROOM, ownerWebId: ME });
    });

    it('the desktop join box hands a browser-build invite to the pod join', () => {
        const link = 'https://web.example/app/?join=' + encodeURIComponent(ROOM) + '~' + encodeURIComponent(BOB) + '&mode=web';
        expect(inviteJoinCommand(link, 'http://127.0.0.1:8080')).toEqual({ cmd: 'join_web_room', code: link });
    });
});

describe('routing', () => {
    it('leaves gateway rooms and the bridge commands alone', () => {
        const h = harness();
        expect(h.cr.route({ cmd: 'send_room', room_id: 'gw-room', content: 'x' })).toBe(false);
        expect(h.cr.route({ cmd: 'pod_room_register', room_id: ROOM, owner_webid: BOB })).toBe(false);
        expect(h.cr.route({ cmd: 'chat_room_create', name: 'x' })).toBe(false);
    });

    it('a web invite with no pod bridge says the pod is needed', async () => {
        const h = harness();
        const code = 'https://web.example/app/?join=' + ROOM + '~' + encodeURIComponent(BOB);
        expect(h.cr.route({ cmd: 'join_web_room', code })).toBe(true);
        expect(h.events.at(-1)).toEqual({ type: 'join_invalid_invite', reason: 'no_pod' });
        h.cr.onState({ ready: true, started: true, webid: ME, pod_root: ROOT });
        h.cr.route({ cmd: 'join_web_room', code });
        await settle();
        expect(h.bridge.io.podDropJoin).toHaveBeenCalledWith(BOB_ROOT, expect.objectContaining({ kind: 'join_request', room_id: ROOM, from_webid: ME }));
        expect(h.events.at(-1)).toEqual({ type: 'join_request_sent', room_id: ROOM, owner_webid: BOB });
    });
});

describe('joining a browser-build room', () => {
    it('registers the container after an approval we asked for, then lists it', async () => {
        const h = harness();
        await joinBobsRoom(h);
        expect(h.sent.filter((m) => m.cmd === 'pod_room_register')).toEqual([{ cmd: 'pod_room_register', room_id: ROOM, owner_webid: BOB }]);
        expect(h.bridge.io.podWriteJoinedRooms).toHaveBeenCalledWith([expect.objectContaining({ roomId: ROOM, owner: BOB, container: CONTAINER })]);
        expect(h.bridge.io.podDeletePendingJoin).toHaveBeenCalledWith(ROOM, BOB);
        expect(h.rooms).toEqual([{ rec: expect.objectContaining({ roomId: ROOM, container: CONTAINER, owned: false }), info: { joined: true } }]);
        expect(h.cr.isPodRoom(ROOM)).toBe(true);
    });

    it('ignores an approval nobody asked for', async () => {
        const h = harness({ pending: false });
        await joinBobsRoom(h);
        expect(h.sent.some((m) => m.cmd === 'pod_room_register')).toBe(false);
        expect(h.rooms).toEqual([]);
    });

    it('keeps an approval and pending marker until the joined room is saved durably', async () => {
        const h = harness();
        h.bridge.io.podWriteJoinedRooms.mockResolvedValueOnce(false);
        await joinBobsRoom(h);
        expect(h.rooms).toEqual([]);
        expect(h.bridge.io.podDeletePendingJoin).not.toHaveBeenCalled();
        expect(h.bridge.io.podDeleteJoin).not.toHaveBeenCalled();
        await h.cr.drainJoins();
        expect(h.cr.isPodRoom(ROOM)).toBe(true);
        expect(h.bridge.io.podDeletePendingJoin).toHaveBeenCalledWith(ROOM, BOB);
        expect(h.bridge.io.podDeleteJoin).toHaveBeenCalledTimes(1);
    });

    it('retries an approval when its pending marker could not be read', async () => {
        const h = harness();
        h.bridge.io.podHasPendingJoin.mockResolvedValueOnce(null);
        await joinBobsRoom(h);
        expect(h.rooms).toEqual([]);
        expect(h.bridge.io.podDeleteJoin).not.toHaveBeenCalled();
        await h.cr.drainJoins();
        expect(h.cr.isPodRoom(ROOM)).toBe(true);
    });

    it('restores an invited room from its saved record in a fresh client', async () => {
        const first = harness();
        let saved = [];
        first.bridge.io.podWriteJoinedRooms.mockImplementation(async (list) => { saved = structuredClone(list); return true; });
        await joinBobsRoom(first);
        const reload = harness({ joined: saved });
        reload.cr.onState({ ready: true, started: true, webid: ME, pod_root: ROOT });
        await until(() => reload.cr.isPodRoom(ROOM));
        expect(reload.cr.room(ROOM)).toMatchObject({ roomId: ROOM, container: CONTAINER, owned: false });
        expect(reload.sent.filter((m) => m.cmd === 'pod_room_register')).toHaveLength(1);
    });

    it('migrates this account cache before registering a room from a damaged legacy list', async () => {
        const rec = { roomId: ROOM, owner: BOB, ownerPodRoot: BOB_ROOT, container: CONTAINER, title: 'Bob room' };
        let saved = false;
        const h = harness({
            storage: { getItem: (key) => key === joinedCacheKey(ME) ? JSON.stringify([rec]) : null },
            register: () => ({ ok: saved, reason: saved ? '' : 'not_joined' }),
        });
        h.bridge.io.podReadJoinedRooms.mockResolvedValue(LEGACY_JOINED_ROOMS_UNREADABLE);
        h.bridge.io.podWriteJoinedRooms.mockImplementation(async (list) => { saved = list.length === 1; return true; });
        h.cr.onState({ ready: true, started: true, webid: ME, pod_root: ROOT });
        await until(() => h.cr.isPodRoom(ROOM));
        expect(h.bridge.io.podWriteJoinedRooms).toHaveBeenCalledWith([rec]);
        expect(h.cr.room(ROOM)).toMatchObject({ ...rec, owned: false });
    });

    it('says why when the gateway pod uses a bearer token', async () => {
        const h = harness({ register: () => ({ ok: false, reason: 'bearer' }) });
        await joinBobsRoom(h);
        expect(h.notes).toEqual([{ kind: 'bearer', roomId: ROOM }]);
        expect(h.rooms).toEqual([]);
    });

    it('appends a message as our WebID and echoes it as ours', async () => {
        const h = harness();
        await joinBobsRoom(h);
        expect(h.cr.route({ cmd: 'send_room', room_id: ROOM, message_id: 'm1', content: 'hello bob' })).toBe(true);
        await until(() => h.events.some((e) => e.type === 'message'));
        const patch = fetches(h, 'PATCH')[0];
        expect(patch.url.startsWith(CONTAINER)).toBe(true);
        expect(patch.body).toContain(`<${ME}>`);
        expect(patch.body).toContain('hello bob');
        expect(h.events.find((e) => e.type === 'message')).toMatchObject({ message_id: 'm1', thread_id: ROOM, from_webid: 'did:key:zDevice' });
    });

    it('a denied append confirms room access and our own session before removal', async () => {
        const h = harness({ routes: (m, url) => {
            if (m === 'PATCH' && url.startsWith(CONTAINER)) return { status: 403 };
            if (m === 'HEAD' && url === CONTAINER + 'members.jsonld') return { status: 403 };
            if (m === 'HEAD' && url === ROOT + 'proxion/joined-rooms.json') return { status: 200 };
            return null;
        } });
        await joinBobsRoom(h);
        h.cr.route({ cmd: 'send_room', room_id: ROOM, message_id: 'm2', content: 'still here?' });
        await until(() => h.removed.length > 0);
        expect(h.removed).toEqual([ROOM]);
        expect(h.cr.isPodRoom(ROOM)).toBe(false);
        expect(h.sent.some((m) => m.cmd === 'pod_room_unregister' && m.container === CONTAINER)).toBe(true);
        expect(h.events.some((e) => e.type === 'message')).toBe(false);
    });

    it('a 403 from the bridge policy is not a removal', async () => {
        const h = harness({ routes: (m, url) => (m === 'PATCH' && url.startsWith(CONTAINER) ? { status: 403, error: 'path_not_allowed' } : null) });
        await joinBobsRoom(h);
        h.cr.route({ cmd: 'send_room', room_id: ROOM, message_id: 'm3', content: 'x' });
        await until(() => fetches(h, 'PATCH').length > 0);
        await settle(30);
        expect(h.removed).toEqual([]);
        expect(h.cr.isPodRoom(ROOM)).toBe(true);
    });
});

describe('hosting a room', () => {
    it.each(['index', 'descriptor', 'private-acl', 'chat-acl', 'members'])('does not report a room as created when %s persistence fails', async (failure) => {
        const h = harness({ routes: (method, url) => {
            if (method !== 'PUT') return null;
            const match = {
                index: url.endsWith('/index.ttl'),
                descriptor: url.endsWith('/room.json'),
                'private-acl': url.startsWith(ROOT + 'rooms/') && url.endsWith('/.acl'),
                'chat-acl': url.startsWith(ROOT + 'proxion/rooms/') && url.endsWith('/.acl'),
                members: url.endsWith('/members.jsonld'),
            };
            return match[failure] ? { status: 500 } : null;
        } });
        expect(await h.cr.create('Team')).toBe(null);
        expect(h.rooms).toEqual([]);
        expect(h.cr.list()).toEqual([]);
    });

    it.each(['descriptor', 'chat-acl', 'members', 'delivery'])('retains the request and repairs membership after %s approval failure', async (failure) => {
        const JOINER = 'https://pods.example/carol/profile/card#me';
        let desc = null, failing = false;
        const h = harness({ routes: (method, url, msg) => {
            if (method === 'PUT') {
                if (failing && ((failure === 'descriptor' && url.endsWith('/room.json'))
                    || (failure === 'chat-acl' && url.startsWith(ROOT + 'proxion/rooms/') && url.endsWith('/.acl'))
                    || (failure === 'members' && url.endsWith('/members.jsonld')))) return { status: 500 };
                if (url.endsWith('/room.json')) { desc = msg.body; return { status: 205 }; }
            }
            if (method === 'GET' && url.endsWith('/room.json')) return desc ? { status: 200, body: desc } : { status: 404 };
            return null;
        } });
        h.cr.onState({ ready: true, started: true, webid: ME, pod_root: ROOT });
        const created = await h.cr.create('Team');
        expect(created).toBeTruthy();
        failing = true;
        if (failure === 'delivery') h.bridge.io.podDropJoin.mockResolvedValueOnce(false);
        h.bridge.io.podReadJoins.mockResolvedValue([{ url: ROOT + 'proxion/join-inbox/retry', msg: {
            kind: 'join_request', room_id: created.roomId, from_webid: JOINER,
        } }]);
        h.bridge.io.podDeleteJoin = vi.fn(async () => true);
        await h.cr.drainJoins();
        expect(h.bridge.io.podDeleteJoin).not.toHaveBeenCalled();
        if (failure !== 'delivery') expect(h.bridge.io.podDropJoin).not.toHaveBeenCalled();
        failing = false;
        await h.cr.drainJoins();
        expect(h.bridge.io.podDeleteJoin).toHaveBeenCalledTimes(1);
        const acl = fetches(h, 'PUT').filter((m) => m.url === `${ROOT}proxion/rooms/${created.roomId}/.acl`).at(-1);
        expect(acl.body).toContain(`<${JOINER}>`);
        expect(h.bridge.io.podDropJoin).toHaveBeenLastCalledWith('https://pods.example/carol/', expect.objectContaining({ kind: 'join_approved' }));
    });

    it('creates the chat, descriptor and owner-only ACLs in our pod', async () => {
        const h = harness();
        h.cr.onState({ ready: true, started: true, webid: ME, pod_root: ROOT });
        expect(h.cr.route({ cmd: 'chat_room_create', name: 'Team', pod: true })).toBe(true);
        await until(() => h.events.some((e) => e.type === 'room_created'));
        const created = h.events.find((e) => e.type === 'room_created');
        expect(created).toMatchObject({ name: 'Team', pod_room: true });
        expect(created.invite_url.startsWith('https://web.example/app/?join=')).toBe(true);
        const id = created.room_id;
        const desc = fetches(h, 'PUT').find((m) => m.url === `${ROOT}rooms/${id}/room.json`);
        expect(JSON.parse(desc.body)).toMatchObject({ room_id: id, owner: ME, title: 'Team' });
        expect(fetches(h, 'PUT').some((m) => m.url === `${ROOT}proxion/rooms/${id}/index.ttl`)).toBe(true);
        const acl = fetches(h, 'PUT').find((m) => m.url === `${ROOT}proxion/rooms/${id}/.acl`);
        expect(acl.body).toContain(`<${ME}>`);
        expect(acl.body).not.toContain('acl:Append');
        // Nothing goes anywhere but our own pod.
        expect(fetches(h).every((m) => m.url.startsWith(ROOT))).toBe(true);
    });

    it('approves a join by granting Read + Append, and a kick takes it away', async () => {
        const JOINER = 'https://pods.example/carol/profile/card#me';
        let desc = null;
        const h = harness({
            routes: (m, url, msg) => {
                if (url.endsWith('/room.json') && m === 'PUT') { desc = msg.body; return { status: 205 }; }
                if (url.endsWith('/room.json') && m === 'GET') return desc ? { status: 200, body: desc } : { status: 404 };
                return null;
            },
        });
        h.cr.onState({ ready: true, started: true, webid: ME, pod_root: ROOT });
        h.cr.route({ cmd: 'chat_room_create', name: 'Team', pod: true });
        await until(() => h.events.some((e) => e.type === 'room_created'));
        const id = h.events.find((e) => e.type === 'room_created').room_id;
        const req = { kind: 'join_request', room_id: id, from_webid: JOINER, from_display_name: 'Carol' };
        h.bridge.io.podReadJoins = vi.fn(async () => [{ url: ROOT + 'proxion/join-inbox/r1', msg: req }]);
        h.bridge.io.podDeleteJoin = vi.fn(async () => true);
        await h.cr.drainJoins();
        await settle(40);
        const acls = () => fetches(h, 'PUT').filter((m) => m.url === `${ROOT}proxion/rooms/${id}/.acl`);
        expect(acls().at(-1).body).toContain(`<${JOINER}>`);
        expect(acls().at(-1).body).toContain('acl:Append');
        expect(h.bridge.io.podDropJoin).toHaveBeenCalledWith('https://pods.example/carol/',
            expect.objectContaining({ kind: 'join_approved', room_id: id, owner_webid: ME, owner_pod_root: ROOT }));
        expect(h.cr.route({ cmd: 'kick_member', room_id: id, webid: JOINER })).toBe(true);
        await until(() => h.events.at(-1).type === 'member_kicked');
        expect(acls().at(-1).body).not.toContain(`<${JOINER}>`);
        expect(h.events.at(-1)).toEqual({ type: 'member_kicked', room_id: id, webid: JOINER });
    });
});


describe('idle joined-room access and cleanup', () => {
    function accessRoutes({ peer = () => 403, own = () => 200 } = {}) {
        return (method, url) => {
            if (method === 'HEAD' && url === CONTAINER + 'members.jsonld') return { status: peer() };
            if (method === 'HEAD' && url === ROOT + 'proxion/joined-rooms.json') return { status: own() };
            return null;
        };
    }

    it('detects removal while idle without appending another message', async () => {
        const h = harness({ routes: accessRoutes() });
        await joinBobsRoom(h);
        expect(await h.cr.checkAccess(ROOM)).toBe('denied');
        expect(h.removed).toEqual([]);
        expect(await h.cr.checkAccess(ROOM)).toBe('removed');
        expect(h.removed).toEqual([ROOM]);
        expect(h.cr.isPodRoom(ROOM)).toBe(false);
        expect(fetches(h, 'PATCH')).toEqual([]);
        expect(h.bridge.io.podWriteJoinedRooms).toHaveBeenLastCalledWith([]);
    });

    it('keeps the room when our own session cannot read private storage', async () => {
        const h = harness({ routes: accessRoutes({ own: () => 401 }) });
        await joinBobsRoom(h);
        expect(await h.cr.checkAccess(ROOM, { denied: true })).toBe('unavailable');
        expect(await h.cr.checkAccess(ROOM)).toBe('unavailable');
        expect(h.removed).toEqual([]);
        expect(h.cr.isPodRoom(ROOM)).toBe(true);
    });

    it('retains tracking until a confirmed removal is saved durably', async () => {
        const h = harness({ routes: accessRoutes() });
        await joinBobsRoom(h);
        h.bridge.io.podWriteJoinedRooms.mockResolvedValueOnce(false);
        expect(await h.cr.checkAccess(ROOM, { denied: true })).toBe('denied');
        expect(h.cr.isPodRoom(ROOM)).toBe(true);
        expect(h.removed).toEqual([]);
        expect(h.sent.some((m) => m.cmd === 'pod_room_unregister')).toBe(false);
        expect(await h.cr.checkAccess(ROOM)).toBe('removed');
        expect(h.cr.isPodRoom(ROOM)).toBe(false);
        expect(h.removed).toEqual([ROOM]);
    });

    it('does not treat an append-only denial as removal while membership remains readable', async () => {
        const h = harness({ routes: (method, url) => {
            if (method === 'PATCH' && url.startsWith(CONTAINER)) return { status: 403 };
            if (method === 'HEAD' && url === CONTAINER + 'members.jsonld') return { status: 200 };
            return null;
        } });
        await joinBobsRoom(h);
        h.cr.route({ cmd: 'send_room', room_id: ROOM, message_id: 'blocked', content: 'cannot append' });
        await until(() => fetches(h, 'HEAD').some((m) => m.url === CONTAINER + 'members.jsonld'));
        await settle();
        expect(h.removed).toEqual([]);
        expect(h.cr.isPodRoom(ROOM)).toBe(true);
    });

    it('stops access checks during disconnect and tracks registered rooms again on reconnect', async () => {
        const setAccessTimer = vi.fn(() => 9), clearAccessTimer = vi.fn();
        const h = harness({ routes: accessRoutes({ peer: () => 200 }), setAccessTimer, clearAccessTimer });
        await joinBobsRoom(h);
        expect(setAccessTimer).toHaveBeenCalledOnce();
        h.cr.onState({ ready: false, started: false });
        expect(clearAccessTimer).toHaveBeenCalledWith(9);
        expect(await h.cr.checkAccess(ROOM)).toBe('stale');
        h.cr.onState({ ready: true, started: true, webid: ME, pod_root: ROOT });
        await until(() => setAccessTimer.mock.calls.length === 2);
        expect(await h.cr.checkAccess(ROOM)).toBe('allowed');
    });

    it('does not lose the room after a failed voluntary leave write', async () => {
        const h = harness();
        await joinBobsRoom(h);
        h.bridge.io.podWriteJoinedRooms.mockResolvedValueOnce(false);
        h.cr.route({ cmd: 'leave_local_room', room_id: ROOM });
        await until(() => h.events.some((e) => e.type === 'error' && e.cmd === 'leave_local_room'));
        expect(h.cr.isPodRoom(ROOM)).toBe(true);
        expect(h.events.some((e) => e.type === 'left_room')).toBe(false);
        h.cr.route({ cmd: 'leave_local_room', room_id: ROOM });
        await until(() => h.events.some((e) => e.type === 'left_room'));
        expect(h.cr.isPodRoom(ROOM)).toBe(false);
    });
});
