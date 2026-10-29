import { describe, it, expect, vi } from 'vitest';
import { createPodSocket } from './podtransport.js';
import { reconcileRoomHistory } from './longchat.js';

const SELF = 'https://me.example/profile/card#me';

function roomsHarness({ descs = [], joined = [], rooms = {} } = {}) {
    const events = [];
    const pod = {
        podListOwnedRoomDescriptors: vi.fn(async () => descs),
        podRegisterRoomChat: vi.fn(async () => true),
    };
    const sock = createPodSocket({
        getSelfWebId: () => SELF,
        handleEvent: (e) => events.push(e),
        pod,
        rooms: { listJoined: vi.fn(async () => joined), ...rooms },
        makeRoomInvite: (id) => 'https://app/?join=' + id,
    });
    return { sock, events, pod };
}

describe('PodSocket rooms in the browser build', () => {
    it('get_rooms rebuilds the invite link for owned rooms and lists joined rooms with their record', async () => {
        const rec = { roomId: 'j', owner: 'https://o/#me', ownerPodRoot: 'https://o/', container: 'https://o/proxion/rooms/j/', title: 'Joined' };
        const { sock, events } = roomsHarness({ descs: [{ room_id: 'a', title: 'Alpha', owner: SELF }], joined: [rec] });
        await sock._route({ cmd: 'get_rooms' });
        expect(events[0].rooms).toEqual([
            { id: 'a', name: 'Alpha', invite_url: 'https://app/?join=a', local: true, creator_webid: SELF },
            { id: 'j', name: 'Joined', invite_url: '', local: true, creator_webid: 'https://o/#me', remote: rec },
        ]);
    });

    it('chat_room_create lists the room in the type index before it has a message', async () => {
        const { sock, events, pod } = roomsHarness();
        await sock._route({ cmd: 'chat_room_create', name: 'Empty' });
        expect(pod.podRegisterRoomChat).toHaveBeenCalledWith(events[0].room_id);
    });

    it('join_request_sent carries the owner, so the pending state can be restored', async () => {
        const events = [];
        const sock = createPodSocket({
            getSelfWebId: () => SELF, handleEvent: (e) => events.push(e), pod: {},
            join: { requestJoin: vi.fn(async () => true) },
        });
        await sock._route({ cmd: 'join_room', code: 'r1~' + encodeURIComponent('https://o.example/#me') });
        expect(events[0]).toEqual({ type: 'join_request_sent', room_id: 'r1', owner_webid: 'https://o.example/#me' });
    });

    it('leave, delete and members are answered from the rooms facade', async () => {
        const { sock, events } = roomsHarness({ rooms: {
            leave: vi.fn(async (id) => id === 'j'),
            deleteOwned: vi.fn(async (id) => id === 'a'),
            members: vi.fn(async () => [{ webid: SELF, role: 'owner' }]),
        } });
        await sock._route({ cmd: 'leave_local_room', room_id: 'a' });   // owned: not left
        await sock._route({ cmd: 'leave_local_room', room_id: 'j' });
        await sock._route({ cmd: 'delete_room', room_id: 'a' });
        await sock._route({ cmd: 'get_room_members', room_id: 'a' });
        expect(events).toEqual([
            { type: 'left_room', room_id: 'j' },
            { type: 'room_deleted', room_id: 'a' },
            { type: 'room_members', room_id: 'a', members: [{ webid: SELF, role: 'owner' }], from_pod: true },
        ]);
    });
});

describe('reconcileRoomHistory with two pod copies of one message', () => {
    it('takes the sender name from whichever copy has it', () => {
        const out = reconcileRoomHistory([], [
            { message_id: 'm', content: 'hi', timestamp: '2026-10-29T10:00:00Z', from_webid: SELF, from_display_name: '' },
            { message_id: 'm', content: 'hi', timestamp: '2026-10-29T10:00:00Z', from_webid: SELF, from_display_name: 'Me' },
        ]);
        expect(out).toHaveLength(1);
        expect(out[0].from_display_name).toBe('Me');
    });
});
