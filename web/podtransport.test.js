import { describe, it, expect, vi } from 'vitest';
import {
    createPodSocket, roomsFromDescriptors, genRoomId,
    POD_SOCKET_OPEN, POD_SOCKET_CLOSED,
} from './podtransport.js';

function harness(descs = []) {
    const events = [];
    const pod = { podListOwnedRoomDescriptors: vi.fn(async () => descs) };
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
        expect(events).toEqual([]);   // no echo -> stays pending -> send-status marks it failed
    });

    it('does NOT echo a fanout when nothing was dropped', async () => {
        const events = [];
        const dm = { dropFanout: vi.fn(async () => false) };
        const sock = createPodSocket({
            getSelfWebId: () => 'https://me', handleEvent: (e) => events.push(e),
            pod: { podListOwnedRoomDescriptors: vi.fn() }, dm,
        });
        await sock._route({ cmd: 'send_dm_fanout', message_id: 'm2', fanout: [{ to_webid: 'https://bob', to_device_id: 'd', payload: {} }] });
        expect(events).toEqual([]);
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

    it('ignores unsupported commands without throwing', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'start_tunnel' });
        await sock._route({ cmd: 'set_presence', status: 'online' });
        expect(events).toEqual([]);
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
