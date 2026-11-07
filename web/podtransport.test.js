import { describe, it, expect, vi } from 'vitest';
import {
    createPodSocket, roomsFromDescriptors, genRoomId,
    POD_SOCKET_OPEN, POD_SOCKET_CLOSED, searchLoadedRooms,
} from './podtransport.js';

function harness(descs = []) {
    const events = [];
    const pod = {
        podRegisterRoomChat: vi.fn(async () => true), podListOwnedRoomDescriptors: vi.fn(async () => descs) };
    const sock = createPodSocket({
        getSelfWebId: () => 'https://me.example/profile/card#me',
        handleEvent: (e) => events.push(e),
        pod,
    });
    return { sock, events, pod };
}

describe('genRoomId', () => {
    it('produces an id that satisfies the pod SAFE_ID_RE', () => {
        for (let i = 0; i < 20; i++) {
            expect(genRoomId()).toMatch(/^[\w-]{1,128}$/);
        }
    });
});

describe('roomsFromDescriptors', () => {
    it('maps descriptors to the rooms-event shape (all local)', () => {
        const rooms = roomsFromDescriptors(
            [{ room_id: 'a', title: 'Alpha', owner: 'https://o' }], 'https://me');
        expect(rooms).toEqual([
            { id: 'a', name: 'Alpha', invite_url: '', local: true, creator_webid: 'https://o' },
        ]);
    });
    it('falls back to id for a missing title and self for owner', () => {
        expect(roomsFromDescriptors([{ room_id: 'x' }], 'https://me')[0])
            .toMatchObject({ name: 'x', creator_webid: 'https://me' });
    });
});

describe('PodSocket', () => {
    it('starts OPEN and closes cleanly', () => {
        const { sock } = harness();
        expect(sock.readyState).toBe(POD_SOCKET_OPEN);
        let closed = false;
        sock.onclose = () => { closed = true; };
        sock.close();
        expect(sock.readyState).toBe(POD_SOCKET_CLOSED);
        expect(closed).toBe(true);
    });

    it('get_rooms reads owned descriptors and emits a rooms event', async () => {
        const { sock, events, pod } = harness([{ room_id: 'a', title: 'Alpha', owner: 'https://me.example/profile/card#me' }]);
        await sock._route({ cmd: 'get_rooms' });
        expect(pod.podListOwnedRoomDescriptors).toHaveBeenCalled();
        expect(events).toEqual([{ type: 'rooms', rooms: [
            { id: 'a', name: 'Alpha', invite_url: '', local: true, creator_webid: 'https://me.example/profile/card#me' },
        ] }]);
    });

    it('join_remote_room (a gateway room link) is refused at once as a desktop room', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'join_remote_room', url: 'https://gw.example:8080/?join=abc' });
        expect(events).toEqual([{ type: 'join_invalid_invite', reason: 'desktop_room' }]);
    });

    it('chat_room_create emits room_created with a fresh id', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'chat_room_create', name: 'General', history_mode: 'none' });
        expect(events).toHaveLength(1);
        expect(events[0]).toMatchObject({ type: 'room_created', name: 'General' });
        expect(events[0].room_id).toMatch(/^[\w-]{1,128}$/);
        expect(events[0].code).toBe(events[0].room_id);
    });

    it('send_room echoes a message so the optimistic render confirms', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'send_room', room_id: 'a', message_id: 'm1', content: 'hi' });
        expect(events[0]).toMatchObject({
            type: 'message', message_id: 'm1', thread_id: 'a',
            source: 'local_room', content: 'hi', local: true,
            from_webid: 'https://me.example/profile/card#me',
        });
    });

    it('send_room echoes only after the sender has drawn its pending bubble', async () => {
        const { sock, events } = harness();
        const routed = sock._route({ cmd: 'send_room', room_id: 'a', message_id: 'm1', content: 'hi' });
        expect(events).toEqual([]);   // still in the sender's call stack
        await routed;
        expect(events).toHaveLength(1);
    });

    it('local_dm drops the envelope and echoes to clear pending', async () => {
        const events = [];
        const dropped = [];
        const dm = { dropDm: vi.fn(async (cmd) => { dropped.push(cmd); return true; }) };
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() }, dm,
        });
        await sock._route({ cmd: 'local_dm', target_webid: 'https://bob', message_id: 'm1', content: 'CT', e2e: true });
        expect(dm.dropDm).toHaveBeenCalledOnce();
        expect(dropped[0].target_webid).toBe('https://bob');
        expect(events[0]).toMatchObject({ type: 'message', message_id: 'm1', thread_id: 'https://bob', source: 'local_dm' });
        expect(events[0].local).toBeUndefined();   // ciphertext echo must not touch the DM preview
    });

    it('does NOT echo when a DM drop fails (so it surfaces as not delivered)', async () => {
        const events = [];
        const dm = { dropDm: vi.fn(async () => false) };   // e.g. recipient has no inbox
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() }, dm,
        });
        await sock._route({ cmd: 'local_dm', target_webid: 'https://bob', message_id: 'm1', content: 'CT' });
        expect(dm.dropDm).toHaveBeenCalledOnce();
        expect(events).toEqual([{ type: 'error', cmd: 'local_dm', code: 'E_POD', message: 'pod_operation_failed', message_id: 'm1' }]);   // immediate failure enables Retry
    });

    it('does NOT echo a fanout when nothing was dropped', async () => {
        const events = [];
        const dm = { dropFanout: vi.fn(async () => false) };
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() }, dm,
        });
        await sock._route({ cmd: 'send_dm_fanout', message_id: 'm2', fanout: [{ to_webid: 'https://bob', to_device_id: 'd', payload: {} }] });
        expect(events).toEqual([{ type: 'error', cmd: 'send_dm_fanout', code: 'E_POD', message: 'pod_operation_failed', message_id: 'm2' }]);
    });

    it('routes send_dm_fanout to the DM engine and echoes to clear pending', async () => {
        const events = [];
        const dm = { dropDm: vi.fn(), dropFanout: vi.fn(async () => true) };
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() }, dm,
        });
        await sock._route({ cmd: 'send_dm_fanout', message_id: 'm7', fanout: [{ to_webid: 'https://bob', to_device_id: 'd1', payload: {} }] });
        expect(dm.dropFanout).toHaveBeenCalledOnce();
        expect(events[0]).toMatchObject({ type: 'message', message_id: 'm7' });
    });

    it('routes voice signaling to the call engine, emitting no event', async () => {
        const events = [];
        const calls = { sendSignal: vi.fn(async () => true) };
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() }, calls,
        });
        for (const c of ['voice_invite', 'voice_answer', 'ice_candidate', 'voice_hangup']) {
            await sock._route({ cmd: c, target_webid: 'https://bob' });
        }
        expect(calls.sendSignal).toHaveBeenCalledTimes(4);
        expect(events).toEqual([]);   // signaling is dropped to the pod, not echoed
    });

    it('get_dms emits empty dm lists (no gateway DMs in web mode)', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'get_dms' });
        expect(events.map(e => e.type)).toEqual(['dms', 'local_dms']);
    });

    it('reports unsupported actions while ignoring gateway background sync', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'start_tunnel' });
        await sock._route({ cmd: 'set_presence', status: 'online' });
        expect(events).toEqual([{ type: 'error', cmd: 'start_tunnel', code: 'E_UNSUPPORTED', message: 'transport_unsupported' }]);
    });

    it('send() parses JSON and routes; bad JSON is ignored', () => {
        const { sock, events } = harness();
        expect(() => sock.send('not json')).not.toThrow();
        expect(events).toEqual([]);
    });

    it('a DM echo never carries the ciphertext (the plaintext copy must stay)', async () => {
        const events = [];
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() },
            dm: { dropDm: vi.fn(async () => true), dropFanout: vi.fn(async () => true) },
        });
        await sock._route({ cmd: 'local_dm', target_webid: 'https://bob', message_id: 'm1', content: 'CT', e2e: true });
        await sock._route({ cmd: 'send_dm_fanout', message_id: 'm2', fanout: [] });
        expect(events).toHaveLength(2);
        for (const e of events) {
            expect(e._podEcho).toBe(true);
            expect('content' in e).toBe(false);
        }
    });

    it('get_local_history answers with the stored history', async () => {
        const events = [];
        const history = vi.fn(async (tid, limit) => [{ message_id: 'a', thread_id: tid, content: 'hi', limit }]);
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() }, history,
        });
        await sock._route({ cmd: 'get_local_history', thread_id: 'https://bob', limit: 50 });
        expect(history).toHaveBeenCalledWith('https://bob', 50);
        expect(events).toEqual([{ type: 'local_history', thread_id: 'https://bob', last_read_ts: 0,
            messages: [{ message_id: 'a', thread_id: 'https://bob', content: 'hi', limit: 50 }] }]);
    });

    it('get_local_history still answers (empty) when the store fails', async () => {
        const events = [];
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() },
            history: async () => { throw new Error('idb'); },
        });
        await sock._route({ cmd: 'get_local_history', thread_id: 'r1' });
        expect(events).toEqual([{ type: 'local_history', thread_id: 'r1', last_read_ts: 0, messages: [] }]);
    });

    it('block / unblock / list_blocks keep the block list in the given store', async () => {
        const events = [];
        let saved = ['https://old'];
        const blockStore = { load: () => saved.slice(), save: (a) => { saved = a.slice(); } };
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() }, blockStore,
        });
        await sock._route({ cmd: 'block', webid: 'https://eve' });
        await sock._route({ cmd: 'block', webid: 'https://eve' });
        expect(saved).toEqual(['https://old', 'https://eve']);
        await sock._route({ cmd: 'unblock', webid: 'https://old' });
        expect(saved).toEqual(['https://eve']);
        await sock._route({ cmd: 'list_blocks' });
        expect(events).toEqual([{ type: 'blocks', webids: ['https://eve'] }]);
    });
});


describe('browser search of loaded room messages', () => {
    const message = (id, content, timestamp = '2026-11-06T12:00:00Z') => ({ message_id: id, content, timestamp, from_webid: 'https://alice/#me', from_display_name: 'Alice' });
    it('matches every query word ignoring case and returns newest results without file bytes', () => {
        const out = searchLoadedRooms('HELLO team', [
            { id: 'r1', name: 'One', messages: [message('one', 'hello Team'), message('nope', 'hello friend')] },
            { id: 'r2', name: 'Two', messages: [{ ...message('two', 'Team says hello', '2026-11-06T13:00:00Z'), file: { data: 'secretbytes' } }] },
        ]);
        expect(out.map(m => [m.thread_id, m.message_id, m.thread_name])).toEqual([['r2', 'two', 'Two'], ['r1', 'one', 'One']]);
        expect(out.every(m => !('file' in m))).toBe(true);
    });
    it('uses current edits and deletes over earlier room snapshots and never returns ciphertext', () => {
        const out = searchLoadedRooms('hello', [
            { id: 'r', messages: [message('edit', 'hello old'), message('delete', 'hello gone')] },
            { id: 'r', messages: [message('edit', 'renamed text'), { ...message('delete', 'hello gone'), deleted: true },
                { ...message('sealed', 'hello ciphertext'), e2e: true }, message('current', 'hello current')] },
        ]);
        expect(out.map(m => m.message_id)).toEqual(['current']);
    });
    it('uses the latest complete room snapshot, including messages removed from it', () => {
        const cached = { id: 'r', messages: [message('removed', 'hello'), message('current', 'hello current')] };
        expect(searchLoadedRooms('hello', [cached, { id: 'r', messages: [message('current', 'hello edited')] }]).map(m => m.message_id)).toEqual(['current']);
        expect(searchLoadedRooms('hello', [cached, { id: 'r', messages: [] }])).toEqual([]);
    });
    it('keeps identical message IDs from different rooms and bounds result count', () => {
        const snapshots = ['r1', 'r2'].map(id => ({ id, messages: [message('same', 'hello')] }));
        expect(searchLoadedRooms('hello', snapshots)).toHaveLength(2);
        expect(searchLoadedRooms('hello', snapshots, 1)).toHaveLength(1);
        expect(searchLoadedRooms('   ', snapshots)).toEqual([]);
        expect(searchLoadedRooms('hello', snapshots, 0)).toEqual([]);
    });
    it('emits device-scoped results and allows the host to merge saved DMs', async () => {
        const events = [];
        const searchRooms = vi.fn(async q => searchLoadedRooms(q, [{ id: 'r', name: 'Room', messages: [message('m', 'hello')] }]));
        const sock = createPodSocket({ getSelfWebId: () => 'https://me', handleEvent: e => events.push(e), pod: {}, searchRooms });
        await sock._route({ cmd: 'search', query: ' hello ' });
        expect(searchRooms).toHaveBeenCalledWith('hello');
        expect(events).toEqual([{ type: 'search_results', query: 'hello', results: [expect.objectContaining({ message_id: 'm', thread_id: 'r' })], local_scope: true }]);
    });
    it('finishes a failed search with an error state rather than a false no-matches response', async () => {
        const events = [];
        const sock = createPodSocket({ getSelfWebId: () => 'https://me', handleEvent: e => events.push(e), pod: {}, searchRooms: async () => { throw new Error('unavailable'); } });
        await sock._route({ cmd: 'search', query: 'hello' });
        expect(events).toEqual([{ type: 'search_results', query: 'hello', results: [], local_scope: true, error: 'pod_operation_failed' }]);
    });
});

describe('PodSocket user-operation errors', () => {
    it('does not pretend a DM was delivered when its engine is missing', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'local_dm', target_webid: 'https://bob', message_id: 'unsent' });
        expect(events).toEqual([{ type: 'error', cmd: 'local_dm', code: 'E_UNSUPPORTED', message: 'transport_unsupported', message_id: 'unsent' }]);
    });
    it('correlates thrown failures to the action and original message without exposing its content', async () => {
        const events = [];
        const sock = createPodSocket({ getSelfWebId: () => 'https://me', handleEvent: e => events.push(e), pod: {}, dm: { dropDm: async () => { throw new Error('private token'); } } });
        await sock._route({ cmd: 'local_dm', content: 'private draft', message_id: 'm', thread_id: 'bob', request_id: 'request' });
        expect(events).toEqual([{ type: 'error', cmd: 'local_dm', code: 'E_POD', message: 'pod_operation_failed', message_id: 'm', thread_id: 'bob', request_id: 'request' }]);
    });
    it('ignores routine startup, preferences, read markers, and typing without toasts', async () => {
        const { sock, events } = harness();
        for (const cmd of ['get_identity', 'pod_status', 'get_my_address', 'get_peer_device_keys', 'get_relationships', 'get_account_sync', 'list_friend_requests', 'link_pod', 'read_dm', 'read_room', 'mark_read', 'typing', 'set_presence', 'set_account_pref']) await sock._route({ cmd });
        expect(events).toEqual([]);
    });
    it('reports failed and unsupported room actions instead of silently closing', async () => {
        const events = [];
        const sock = createPodSocket({ getSelfWebId: () => 'https://me', handleEvent: e => events.push(e), pod: {}, rooms: { leave: async () => false } });
        await sock._route({ cmd: 'leave_local_room', room_id: 'r' });
        await sock._route({ cmd: 'delete_room', room_id: 'r' });
        expect(events).toEqual([
            { type: 'error', cmd: 'leave_local_room', code: 'E_POD', message: 'pod_operation_failed', room_id: 'r' },
            { type: 'error', cmd: 'delete_room', code: 'E_UNSUPPORTED', message: 'transport_unsupported', room_id: 'r' },
        ]);
    });
});
