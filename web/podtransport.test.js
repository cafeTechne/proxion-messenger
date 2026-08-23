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

    it('get_dms emits empty dm lists (no gateway DMs in web mode)', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'get_dms' });
        expect(events.map(e => e.type)).toEqual(['dms', 'local_dms']);
    });

    it('ignores unsupported commands without throwing', async () => {
        const { sock, events } = harness();
        await sock._route({ cmd: 'send_dm', content: 'x' });
        await sock._route({ cmd: 'start_tunnel' });
        expect(events).toEqual([]);
    });

    it('send() parses JSON and routes; bad JSON is ignored', () => {
        const { sock, events } = harness();
        expect(() => sock.send('not json')).not.toThrow();
        expect(events).toEqual([]);
    });
});
