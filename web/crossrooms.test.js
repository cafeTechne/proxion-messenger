// crossrooms.js: desktop pod rooms over the gateway's pod bridge. Joining a
// browser-build user's room, hosting one, and the removal path.
import { describe, it, expect, vi } from 'vitest';
import { createCrossRooms, vetJoinedRoom, webAppUrl, WEB_APP_URL } from './crossrooms.js';
import { createGwPodBridge } from './gwpod.js';
import { inviteJoinCommand } from './rooms.js';
import { parseInvite } from './webjoin.js';

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
function harness({ routes = () => null, register = () => ({ ok: true }), pending = true, joined = [] } = {}) {
    const sent = [], events = [], notes = [], rooms = [], removed = [];
    let bridge = null, cr = null;
    const send = (m) => {
        sent.push(m);
        if (m.cmd === 'pod_fetch') {
            const r = routes(m.method, m.url, m) || ((m.method === 'GET' || m.method === 'HEAD') ? { status: 404 } : { status: 201 });
            setTimeout(() => bridge.handleEvent({ type: 'pod_fetch_result', req_id: m.req_id, headers: {}, body: '', ...r }), 0);
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
    bridge.io.podReadJoins = vi.fn(async () => []);
    bridge.io.podEnsureJoinInbox = vi.fn(async () => ROOT + 'proxion/join-inbox/');
    bridge.io.podDropJoin = vi.fn(async () => true);
    bridge.io.podWritePendingJoin = vi.fn(async () => {});
    bridge.io.podFetchPeerSigner = vi.fn(async () => null);
    cr = createCrossRooms({
        bridge, send,
        handleEvent: (ev) => events.push(ev),
        getState: () => ({ ready: true, started: true, webid: ME, pod_root: ROOT }),
        getSelfId: () => 'did:key:zDevice',
        getDisplayName: () => 'Me',
        getSigner: () => null,
        askApprove: vi.fn(async () => 'yes'),
        onRoom: (rec, info) => rooms.push({ rec, info }),
        onRemoved: (id) => removed.push(id),
        notify: (kind, d) => notes.push({ kind, ...d }),
        inviteBase: () => 'https://web.example/app/',
        timeoutMs: 500,
    });
    return { cr, bridge, sent, events, notes, rooms, removed };
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

    it('a 403 from the owner pod means we were removed', async () => {
        const h = harness({ routes: (m, url) => (m === 'PATCH' && url.startsWith(CONTAINER) ? { status: 403 } : null) });
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
