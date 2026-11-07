import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { withoutJoinedRecord } from './webrooms.js';

const source = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
function declaration(name) {
    const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
    const tail = source.slice(match.index);
    const end = /^        }\r?$/m.exec(tail);
    return tail.slice(0, end.index + end[0].length);
}
const SELF = 'https://me.example/profile/card#me';
const ROOM = 'joined-room';
function removalHarness() {
    const room = { owner: 'https://owner.example/#me', container: 'https://owner.example/proxion/rooms/r/' };
    const deps = {
        selfWebId: SELF, _remoteRooms: { [ROOM]: room },
        _updateJoinedRooms: vi.fn(async () => true), withoutJoinedRecord,
        webRoomSync: { untrack: vi.fn() }, webRoomAccess: { untrack: vi.fn() },
        _handleEventAsync: vi.fn(),
        podQueueList: vi.fn(async () => [
            { message_id: 'revoked-write', room_id: ROOM, from_webid: SELF },
            { message_id: 'another-room', room_id: 'other', from_webid: SELF },
            { message_id: 'another-account', room_id: ROOM, from_webid: 'https://other.example/#me' },
        ]),
        podQueueRemove: vi.fn(async () => {}),
    };
    const compiled = new Function(...Object.keys(deps), `${declaration('_webRemovedFromRoom')};
        return { remove: _webRemovedFromRoom, switchAccount(id) { selfWebId = id; } };`)(...Object.values(deps));
    return { ...compiled, deps, room };
}

describe('confirmed browser room removal', () => {
    it('keeps a room and its monitor while the durable removal cannot be saved', async () => {
        const h = removalHarness();
        h.deps._updateJoinedRooms.mockResolvedValueOnce(false);
        expect(await h.remove(ROOM)).toBe(false);
        expect(h.deps._remoteRooms[ROOM]).toBe(h.room);
        expect(h.deps.webRoomSync.untrack).not.toHaveBeenCalled();
        expect(h.deps.webRoomAccess.untrack).not.toHaveBeenCalled();
        expect(h.deps._handleEventAsync).not.toHaveBeenCalled();
        expect(h.deps.podQueueRemove).not.toHaveBeenCalled();
    });
    it('removes the saved membership and only that account’s revoked queued writes', async () => {
        const h = removalHarness();
        expect(await h.remove(ROOM)).toBe(true);
        const change = h.deps._updateJoinedRooms.mock.calls[0][0];
        const other = { roomId: 'other', owner: h.room.owner, ownerPodRoot: 'https://owner.example/', container: h.room.container, title: 'Other' };
        expect(change([{ ...other, roomId: ROOM }, other])).toEqual([other]);
        expect(h.deps._remoteRooms[ROOM]).toBeUndefined();
        expect(h.deps.webRoomAccess.untrack).toHaveBeenCalledWith(ROOM);
        expect(h.deps._handleEventAsync).toHaveBeenCalledWith({ type: 'kicked_from_room', room_id: ROOM }, true);
        await Promise.resolve();
        await Promise.resolve();
        expect(h.deps.podQueueRemove.mock.calls).toEqual([['revoked-write']]);
    });
    it('does not close a replacement room or a changed account after a slow save', async () => {
        for (const accountChanges of [false, true]) {
            const h = removalHarness();
            h.deps._updateJoinedRooms.mockImplementationOnce(async () => {
                if (accountChanges) h.switchAccount('https://other.example/#me');
                else h.deps._remoteRooms[ROOM] = { ...h.room };
                return true;
            });
            expect(await h.remove(ROOM)).toBe(false);
            expect(h.deps._remoteRooms[ROOM]).toBeDefined();
            expect(h.deps._handleEventAsync).not.toHaveBeenCalled();
        }
    });
});

function failedWriteHarness(response, accessState) {
    let write;
    const deps = {
        clientMsgId: 'message-a', _roomId: ROOM,
        _remoteRoom: { container: 'https://owner.example/proxion/rooms/r/' },
        _podMsg: { content: 'Keep my message', from_webid: SELF }, selfWebId: SELF,
        sendStatus: { trackPodWrite: (_id, callback) => { write = callback; } },
        podWriteChatMessageAtResult: vi.fn(async () => response),
        webRoomAccess: { check: vi.fn(async () => accessState) },
        podQueueAdd: vi.fn(), podQueueRemove: vi.fn(), _schedulePodQueueRetry: vi.fn(),
    };
    const start = source.indexOf('                        sendStatus.trackPodWrite(clientMsgId, async () => {', source.indexOf('if (_remoteRoom) {'));
    const end = source.indexOf('                        });', start);
    new Function(...Object.keys(deps), source.slice(start, end + '                        });'.length))(...Object.values(deps));
    return { deps, write };
}

describe('denied browser room writes', () => {
    it.each(['unavailable', 'denied', 'stale', 'allowed'])('preserves queued text when access is %s', async (state) => {
        const h = failedWriteHarness({ ok: false, status: 403 }, state);
        expect(await h.write()).toBe(false);
        expect(h.deps.webRoomAccess.check).toHaveBeenCalledWith(ROOM, { denied: true });
        expect(h.deps.podQueueAdd).toHaveBeenCalledWith(expect.objectContaining({
            message_id: 'message-a', room_id: ROOM, from_webid: SELF,
            msg: { content: 'Keep my message', from_webid: SELF },
        }));
        expect(h.deps._schedulePodQueueRetry).toHaveBeenCalledOnce();
    });
    it('discards a failed write only after access removal is confirmed', async () => {
        const h = failedWriteHarness({ ok: false, status: 403 }, 'removed');
        expect(await h.write()).toBe(false);
        expect(h.deps.podQueueRemove).toHaveBeenCalledWith('message-a');
        expect(h.deps.podQueueAdd).not.toHaveBeenCalled();
    });
    it('does not classify bridge policy failures as room removal', async () => {
        const h = failedWriteHarness({ ok: false, status: 403, policy: true }, 'removed');
        await h.write();
        expect(h.deps.webRoomAccess.check).not.toHaveBeenCalled();
        expect(h.deps.podQueueAdd).toHaveBeenCalledOnce();
    });
});
