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
    it('omits completed closures without repeating cleanup on every reload', async () => {
        const resumeDeletion = vi.fn(async () => true);
        const h = roomsHarness({ descs: [{ room_id: 'closed', owner: SELF, closed: true, deletion_complete: true }],
            rooms: { resumeDeletion } });
        await h.sock._route({ cmd: 'get_rooms' });
        expect(resumeDeletion).not.toHaveBeenCalled();
        expect(h.events).toEqual([{ type: 'rooms', rooms: [] }]);
    });
    it('resumes closed-room cleanup on reload and omits completed rooms', async () => {
        const resumeDeletion = vi.fn(async () => true);
        const h = roomsHarness({ descs: [{ room_id: 'closed', owner: SELF, closed: true }], rooms: { resumeDeletion } });
        await h.sock._route({ cmd: 'get_rooms' });
        expect(resumeDeletion).toHaveBeenCalledWith('closed');
        expect(h.events).toEqual([{ type: 'rooms', rooms: [] }]);
    });

    it('retains a closed owner room for retry when reload cleanup fails', async () => {
        const h = roomsHarness({ descs: [{ room_id: 'closed', owner: SELF, closed: true }],
            rooms: { resumeDeletion: vi.fn(async () => false) } });
        await h.sock._route({ cmd: 'get_rooms' });
        expect(h.events[0]).toMatchObject({ type: 'error', cmd: 'delete_room', room_id: 'closed' });
        expect(h.events[1].rooms).toEqual([{ id: 'closed', name: 'closed', invite_url: '', local: true,
            creator_webid: SELF, closed: true }]);
    });
    it('get_rooms rebuilds the invite link for owned rooms and lists joined rooms with their record', async () => {
        const rec = { roomId: 'j', owner: 'https://o/#me', ownerPodRoot: 'https://o/', container: 'https://o/proxion/rooms/j/', title: 'Joined' };
        const { sock, events } = roomsHarness({ descs: [{ room_id: 'a', title: 'Alpha', owner: SELF }], joined: [rec] });
        await sock._route({ cmd: 'get_rooms' });
        expect(events[0].rooms).toEqual([
            { id: 'a', name: 'Alpha', invite_url: 'https://app/?join=a', local: true, creator_webid: SELF },
            { id: 'j', name: 'Joined', invite_url: '', local: true, creator_webid: 'https://o/#me', remote: rec },
        ]);
    });

    it('kick_member takes a member out of an owned room and says so', async () => {
        const kick = vi.fn(async (roomId) => roomId === 'a');
        const { sock, events } = roomsHarness({ rooms: { kick } });
        await sock._route({ cmd: 'kick_member', room_id: 'a', webid: 'https://x/#me' });
        await sock._route({ cmd: 'kick_member', room_id: 'j', webid: 'https://x/#me' });
        expect(kick).toHaveBeenCalledTimes(2);
        expect(events).toEqual([{ type: 'member_kicked', room_id: 'a', webid: 'https://x/#me' },
            { type: 'error', cmd: 'kick_member', code: 'E_POD', message: 'pod_operation_failed', room_id: 'j' }]);
    });

    it('chat_room_create lists the room in the type index before it has a message', async () => {
        const { sock, events, pod } = roomsHarness();
        await sock._route({ cmd: 'chat_room_create', name: 'Empty' });
        expect(pod.podRegisterRoomChat).toHaveBeenCalledWith(events[0].room_id);
    });

    it('does not expose a created room until its durable writes finish', async () => {
        let finish;
        const createOwned = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
        const { sock, events, pod } = roomsHarness({ rooms: { createOwned } });
        const creating = sock._route({ cmd: 'chat_room_create', name: 'Saved' });
        expect(events).toEqual([]);
        expect(pod.podRegisterRoomChat).not.toHaveBeenCalled();
        finish(true);
        await creating;
        expect(events[0]).toMatchObject({ type: 'room_created', name: 'Saved' });
        expect(createOwned).toHaveBeenCalledWith(events[0].room_id, 'Saved');
    });

    it.each([false, 'throw'])('reports creation failure when saving returns %s', async (failure) => {
        const createOwned = vi.fn(async () => { if (failure === 'throw') throw new Error('offline'); return false; });
        const { sock, events } = roomsHarness({ rooms: { createOwned } });
        await sock._route({ cmd: 'chat_room_create', name: 'Unsaved' });
        expect(events).toEqual([{ type: 'room_create_failed' }]);
    });

    it.each([false, 'throw'])('reports join failure when requesting returns %s', async (failure) => {
        const events = [];
        const sock = createPodSocket({
            getSelfWebId: () => SELF, handleEvent: (e) => events.push(e), pod: {},
            join: { requestJoin: vi.fn(async () => { if (failure === 'throw') throw new Error('offline'); return false; }) },
        });
        await sock._route({ cmd: 'join_room', code: 'r1~' + encodeURIComponent('https://o.example/#me') });
        expect(events).toEqual([{ type: 'join_invalid_invite', reason: 'unreachable' }]);
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
            { type: 'error', cmd: 'leave_local_room', code: 'E_POD', message: 'pod_operation_failed', room_id: 'a' },
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
