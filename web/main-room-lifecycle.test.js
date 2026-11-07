import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { buildRoomDescriptor, withMembers } from './roomdesc.js';
import { withJoinedRecord, withoutJoinedRecord } from './webrooms.js';
import { createOwnerMembership } from './room-membership.js';

// Execute the composition root's actual handlers with pod/UI dependencies
// injected. Importing main.js itself would boot the entire browser application.
const source = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
function handlers(names, deps) {
    const declarations = names.map((name) => {
        const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
        if (!match) throw new Error(`Missing main.js handler: ${name}`);
        const tail = source.slice(match.index);
        const end = /^        }\r?$/m.exec(tail);
        if (!end) throw new Error(`Missing closing brace for ${name}`);
        return tail.slice(0, end.index + end[0].length);
    });
    return new Function(...Object.keys(deps), `${declarations.join('\n')}\nreturn { ${names.join(', ')} };`)(...Object.values(deps));
}

const OWNER = 'https://owner.example/profile/card#me';
const JOINER = 'https://joiner.example/profile/card#me';
const ROOT = 'https://owner.example/';
const ROOM = 'room-130';
const CONTAINER = ROOT + 'chats/' + ROOM + '/';

it('clears old pod-room UI and cached previews when the connected account changes', () => {
    const remove = vi.fn();
    const deps = { document: { getElementById: () => ({ remove }) },
        roomCreatorOf: new Set([ROOM, 'gateway-room']), roomInviteUrls: { [ROOM]: 'old-invite' },
        _local_rooms: { [ROOM]: {}, 'gateway-room': {} }, _webRoomCache: { [ROOM]: ['private'] },
        roomLastMessages: { [ROOM]: { snippet: 'private' } }, activeView: { id: ROOM },
        updatePageTitle: vi.fn(), _showThreadClosed: vi.fn(), t: key => key,
    };
    handlers(['_clearCrossRoomSession'], deps)._clearCrossRoomSession([{ roomId: ROOM }]);
    expect(remove).toHaveBeenCalledOnce();
    expect(deps.roomCreatorOf.has(ROOM)).toBe(false);
    expect(deps.roomCreatorOf.has('gateway-room')).toBe(true);
    expect(deps._local_rooms).toEqual({ 'gateway-room': {} });
    expect(deps._webRoomCache).toEqual({});
    expect(deps.roomLastMessages).toEqual({});
    expect(deps.roomInviteUrls).toEqual({});
    expect(deps._showThreadClosed).toHaveBeenCalledWith('heading.welcome');
});

it.each([false, true])('cancels an old account prompt without dismissing a replacement (replaced=%s)', async replaced => {
    let current = true, tick, cancel;
    const ok = { onclick: null }, modal = { style: { display: 'flex' } };
    const cancelButton = { click: vi.fn(() => { modal.style.display = 'none'; cancel(); }) };
    const deps = {
        document: { getElementById: id => ({ 'confirm-ok': ok, 'confirm-modal': modal, 'confirm-cancel': cancelButton })[id] },
        showConfirm: (_message, yes, no) => { ok.onclick = yes; cancel = no; },
        setInterval: fn => { tick = fn; return 1; }, clearInterval: vi.fn(),
    };
    const app = handlers(['_askJoinOnce'], deps);
    const answer = app._askJoinOnce('Join?', {}, () => current);
    if (replaced) ok.onclick = () => {};
    current = false; tick();
    expect(await answer).toBe(null);
    expect(cancelButton.click).toHaveBeenCalledTimes(replaced ? 0 : 1);
    expect(deps.clearInterval).toHaveBeenCalledWith(1);
});
const descriptor = () => buildRoomDescriptor({ roomId: ROOM, title: 'Friends', owner: OWNER });
const joinedRecord = () => ({ roomId: ROOM, owner: OWNER, ownerPodRoot: ROOT, container: CONTAINER, title: 'Friends' });

function ownerHarness() {
    let persisted = descriptor(), version = 0;
    const ownerMembership = {};
    const deps = {
        selfWebId: OWNER,
        withMembers,
        podReadRoomDescriptor: vi.fn(async () => structuredClone(persisted)),
        podReadRoomDescriptorVersion: vi.fn(async () => ({ ok: true, descriptor: structuredClone(persisted), etag: `"v${version}"` })),
        signRoomDescriptor: vi.fn(async (d) => d),
        podWriteRoomDescriptor: vi.fn(async (d) => { persisted = structuredClone(d); version++; return true; }),
        podWriteRoomDescriptorVersion: vi.fn(async (d, snapshot) => {
            if (snapshot.etag !== `"v${version}"`) return { ok: false, conflict: true, status: 412 };
            persisted = structuredClone(d); version++;
            return { ok: true, status: 200 };
        }),
        _ownerMembership: ownerMembership,
        roomCreatorOf: new Set([ROOM]), _remoteRooms: {},
        podStorageRoot: () => ROOT,
        chatRootUrl: () => CONTAINER,
        podGrantChatParticipants: vi.fn(async () => true),
        podWriteRoomMembers: vi.fn(async () => true),
        podDeleteOwnedRoom: vi.fn(async () => true),
        webRoomSync: { untrack: vi.fn() },
        _local_rooms: {},
        window: { proxionWebJoin: { sendApproval: vi.fn(async () => true), sendDecline: vi.fn(async () => true) } },
        _queueJoinPrompt: (task) => task(),
        _joinRequesterLabel: vi.fn(async () => 'Joiner'),
        _askJoinOnce: vi.fn(async () => 'yes'),
        t: (key) => key,
        showToast: vi.fn(),
    };
    const app = handlers(['_applyWebRoomMembers', '_approveWebJoin', '_onWebJoinRequest', '_webKickMember',
        '_webDeleteRoom', '_finishWebRoomDeletion'], deps);
    Object.assign(ownerMembership, createOwnerMembership({
        read: deps.podReadRoomDescriptorVersion, write: deps.podWriteRoomDescriptorVersion,
        getOwnerId: () => deps.selfWebId,
        signDescriptor: (desc) => deps.signRoomDescriptor({ ...desc, name: desc.title, code: desc.room_id,
            creator_webid: desc.owner, created_at: desc.created }),
        apply: app._applyWebRoomMembers,
    }));
    return { deps, persisted: () => persisted, ...app };
}

describe('browser room owner approval', () => {
    it('closes and revokes a room before removing discovery, then resumes failed cleanup', async () => {
        const h = ownerHarness();
        await h._approveWebJoin(JOINER, ROOM);
        h.deps.podDeleteOwnedRoom.mockImplementationOnce(async () => {
            expect(h.persisted().closed).toBe(true);
            expect(h.deps.podGrantChatParticipants).toHaveBeenLastCalledWith(CONTAINER, OWNER, [], expect.objectContaining({ check: expect.any(Function) }));
            return false;
        });
        expect(await h._webDeleteRoom(ROOM)).toBe(false);
        expect(h.persisted()).toMatchObject({ closed: true, deletion_complete: false });
        expect(h.deps.showToast).toHaveBeenCalledWith('error.generic', 'error');
        expect(h.deps.webRoomSync.untrack).not.toHaveBeenCalled();
        expect(await h._finishWebRoomDeletion(ROOM)).toBe(true);
        expect(h.persisted().deletion_complete).toBe(true);
        expect(h.deps.webRoomSync.untrack).toHaveBeenCalledWith(ROOM);
    });

    it('declines requests for a closed room without an approval prompt', async () => {
        const h = ownerHarness();
        expect(await h._webDeleteRoom(ROOM)).toBe(true);
        expect(await h._onWebJoinRequest({ room_id: ROOM, from_webid: JOINER })).toBe(true);
        expect(h.deps.window.proxionWebJoin.sendDecline).toHaveBeenCalledWith(JOINER, ROOM);
        expect(h.deps._askJoinOnce).not.toHaveBeenCalled();
        expect(await h._approveWebJoin(JOINER, ROOM)).toBe(false);
        expect(h.deps.window.proxionWebJoin.sendApproval).not.toHaveBeenCalled();
    });
    it.each(['descriptor', 'acl', 'members', 'delivery'])('retries a failed %s write before consuming the request', async (stage) => {
        const h = ownerHarness();
        const ops = [h.deps.podWriteRoomDescriptorVersion, h.deps.podGrantChatParticipants,
            h.deps.podWriteRoomMembers, h.deps.window.proxionWebJoin.sendApproval];
        const failedIndex = ['descriptor', 'acl', 'members', 'delivery'].indexOf(stage);
        ops[failedIndex].mockResolvedValueOnce(false);
        const req = { room_id: ROOM, from_webid: JOINER };

        expect(await h._onWebJoinRequest(req)).toBe(false);
        for (const op of ops.slice(failedIndex + 1)) expect(op).not.toHaveBeenCalled();
        expect(h.deps.showToast).not.toHaveBeenCalled();

        expect(await h._onWebJoinRequest(req)).toBe(true);
        expect(h.persisted().members.filter((m) => m.webid === JOINER)).toHaveLength(1);
        expect(h.deps.podGrantChatParticipants).toHaveBeenLastCalledWith(CONTAINER, OWNER, [JOINER], expect.objectContaining({ check: expect.any(Function) }));
        expect(h.deps.podWriteRoomMembers).toHaveBeenLastCalledWith(ROOM, [
            { webid: OWNER, role: 'admin' }, { webid: JOINER, role: 'member' },
        ]);
        expect(h.deps.window.proxionWebJoin.sendApproval).toHaveBeenLastCalledWith(JOINER, ROOM, 'Friends');
        expect(h.deps._local_rooms[ROOM].memberWebIds).toEqual(new Set([OWNER, JOINER]));
        // A partially persisted member retries the grant without another prompt.
        expect(h.deps._askJoinOnce).toHaveBeenCalledTimes(failedIndex === 0 ? 2 : 1);
    });

    it('repairs grants for an existing member before resending approval', async () => {
        const h = ownerHarness();
        await h.deps.podWriteRoomDescriptor(withMembers(h.persisted(), [{ webid: JOINER, role: 'member' }]));

        expect(await h._onWebJoinRequest({ room_id: ROOM, from_webid: JOINER })).toBe(true);
        expect(h.deps._askJoinOnce).not.toHaveBeenCalled();
        expect(h.deps.podGrantChatParticipants).toHaveBeenCalledWith(CONTAINER, OWNER, [JOINER], expect.objectContaining({ check: expect.any(Function) }));
        expect(h.deps.podWriteRoomMembers).toHaveBeenCalled();
        expect(h.deps.window.proxionWebJoin.sendApproval).toHaveBeenCalledOnce();
    });

    it('does not re-add someone banned while the approval prompt was open', async () => {
        const h = ownerHarness();
        h.deps._askJoinOnce.mockImplementationOnce(async () => {
            // Another owner action changed the durable membership while this
            // request waited for an answer. The approval must re-read it.
            await h.deps.podWriteRoomDescriptor(withMembers(h.persisted(), [
                { webid: JOINER, role: 'member', banned: true },
            ]));
            h.deps.podWriteRoomDescriptorVersion.mockClear();
            return 'yes';
        });

        await h._onWebJoinRequest({ room_id: ROOM, from_webid: JOINER });

        expect(h.persisted().members.find((m) => m.webid === JOINER)?.banned).toBe(true);
        expect(h.deps.podWriteRoomDescriptorVersion).not.toHaveBeenCalled();
        expect(h.deps.podGrantChatParticipants).not.toHaveBeenCalled();
        expect(h.deps.podWriteRoomMembers).not.toHaveBeenCalled();
        expect(h.deps.window.proxionWebJoin.sendApproval).not.toHaveBeenCalled();
    });
});

describe('browser room owner removal', () => {
    it('reports a failed conditional write without granting or announcing removal', async () => {
        const h = ownerHarness();
        await h.deps.podWriteRoomDescriptor(withMembers(h.persisted(), [{ webid: JOINER, role: 'member' }]));
        h.deps.podWriteRoomDescriptorVersion.mockResolvedValueOnce({ ok: false, status: 503 });
        expect(await h._webKickMember(ROOM, JOINER)).toBe(false);
        expect(h.persisted().members.some((m) => m.webid === JOINER)).toBe(true);
        expect(h.deps.podGrantChatParticipants).not.toHaveBeenCalled();
        expect(h.deps.podWriteRoomMembers).not.toHaveBeenCalled();
        expect(await h._webKickMember(ROOM, JOINER)).toBe(true);
        expect(h.persisted().members.some((m) => m.webid === JOINER)).toBe(false);
        expect(h.deps.podGrantChatParticipants).toHaveBeenLastCalledWith(CONTAINER, OWNER, [], expect.objectContaining({ check: expect.any(Function) }));
    });

    it('does not claim removal when a different owner device re-added the member during access reconciliation', async () => {
        const h = ownerHarness();
        await h.deps.podWriteRoomDescriptor(withMembers(h.persisted(), [{ webid: JOINER, role: 'member' }]));
        h.deps.podGrantChatParticipants.mockImplementationOnce(async () => {
            await h.deps.podWriteRoomDescriptor(withMembers(h.persisted(), [...h.persisted().members, { webid: JOINER, role: 'member' }]));
            return true;
        });
        expect(await h._webKickMember(ROOM, JOINER)).toBe(false);
        expect(h.deps.podGrantChatParticipants).toHaveBeenLastCalledWith(CONTAINER, OWNER, [JOINER], expect.objectContaining({ check: expect.any(Function) }));
        expect(h.persisted().members.some((m) => m.webid === JOINER)).toBe(true);
    });
});

function createHarness() {
    const deps = {
        selfWebId: OWNER,
        buildRoomDescriptor,
        signRoomDescriptor: vi.fn(async (d) => ({ ...d, 'px:sig': 'signed-descriptor' })),
        podWriteRoomDescriptor: vi.fn(async () => true),
        podSetContainerAcl: vi.fn(async () => true),
        podWriteRoomMembers: vi.fn(async () => true),
        podRegisterRoomChat: vi.fn(async () => true),
    };
    const writes = [deps.podWriteRoomDescriptor, deps.podSetContainerAcl,
        deps.podWriteRoomMembers, deps.podRegisterRoomChat];
    return { deps, writes, ...handlers(['_webCreateRoom'], deps) };
}

describe('browser room creation', () => {
    it.each(['descriptor', 'acl', 'members', 'registration'])('does not report success when the %s write fails', async (stage) => {
        const h = createHarness();
        const failedIndex = ['descriptor', 'acl', 'members', 'registration'].indexOf(stage);
        h.writes[failedIndex].mockResolvedValueOnce(false);

        expect(await h._webCreateRoom(ROOM, 'Friends')).toBe(false);
        for (const write of h.writes.slice(failedIndex + 1)) expect(write).not.toHaveBeenCalled();
    });

    it.each(['descriptor', 'acl', 'members', 'registration'])('waits for the %s write before exposing a completed room', async (stage) => {
        const h = createHarness();
        const blockedIndex = ['descriptor', 'acl', 'members', 'registration'].indexOf(stage);
        let release;
        const blocked = new Promise((resolve) => { release = resolve; });
        h.writes[blockedIndex].mockReturnValueOnce(blocked);
        let settled = false;
        const creation = h._webCreateRoom(ROOM, 'Friends');
        creation.then(() => { settled = true; });
        await vi.waitFor(() => expect(h.writes[blockedIndex]).toHaveBeenCalledOnce());

        expect(settled).toBe(false);
        for (const write of h.writes.slice(blockedIndex + 1)) expect(write).not.toHaveBeenCalled();
        release(true);
        expect(await creation).toBe(true);
    });

    it('saves the signed owner descriptor, access rules and member record before succeeding', async () => {
        const h = createHarness();

        expect(await h._webCreateRoom(ROOM, 'Friends')).toBe(true);

        expect(h.deps.podWriteRoomDescriptor).toHaveBeenCalledWith(expect.objectContaining({
            room_id: ROOM, title: 'Friends', name: 'Friends', code: ROOM,
            owner: OWNER, creator_webid: OWNER, 'px:sig': 'signed-descriptor',
            members: [{ webid: OWNER, role: 'owner', banned: false }],
        }));
        expect(h.deps.podSetContainerAcl).toHaveBeenCalledWith(`rooms/${ROOM}/`, OWNER, []);
        expect(h.deps.podWriteRoomMembers).toHaveBeenCalledWith(ROOM, [{ webid: OWNER, role: 'admin' }]);
        expect(h.deps.podRegisterRoomChat).toHaveBeenCalledWith(ROOM);
    });
});

function joinerHarness() {
    let saved = [];
    const deps = {
        podStorageRoot: () => 'https://joiner.example/',
        isPeerPodRootAllowed: () => true,
        peerPodRootFromWebId: () => ROOT,
        podHasPendingJoin: vi.fn(async () => true),
        podReadRoomDescriptorAt: vi.fn(async () => ({ title: 'Friends', long_chat: CONTAINER })),
        _vetRemoteRoom: () => CONTAINER,
        withJoinedRecord,
        _updateJoinedRooms: vi.fn(async (change) => { saved = change(saved); return true; }),
        podDeletePendingJoin: vi.fn(async () => true),
        _clearJoinPendingBanner: vi.fn(),
        _registerRemoteRoom: vi.fn(() => true),
        showToast: vi.fn(),
        t: (key) => key,
    };
    return { deps, saved: () => saved, ...handlers(['_onWebJoinApproved'], deps) };
}
const approval = () => ({ room_id: ROOM, owner_webid: OWNER, owner_pod_root: ROOT, title: 'Friends' });

describe('browser room joiner approval', () => {
    it('retains an approval while the pending marker cannot be read', async () => {
        const h = joinerHarness();
        h.deps.podHasPendingJoin.mockResolvedValueOnce(null);

        expect(await h._onWebJoinApproved(approval())).toBe(false);
        expect(h.deps.podReadRoomDescriptorAt).not.toHaveBeenCalled();
        expect(h.deps.podDeletePendingJoin).not.toHaveBeenCalled();
        expect(h.deps._registerRemoteRoom).not.toHaveBeenCalled();

        await h._onWebJoinApproved(approval());
        expect(h.saved()).toEqual([joinedRecord()]);
        expect(h.deps._registerRemoteRoom).toHaveBeenCalledOnce();
    });

    it('leaves the pending state and approval intact until the joined room is durable', async () => {
        const h = joinerHarness();
        h.deps._updateJoinedRooms.mockResolvedValueOnce(false);

        expect(await h._onWebJoinApproved(approval())).toBe(false);
        expect(h.saved()).toEqual([]);
        expect(h.deps.podDeletePendingJoin).not.toHaveBeenCalled();
        expect(h.deps._clearJoinPendingBanner).not.toHaveBeenCalled();
        expect(h.deps._registerRemoteRoom).not.toHaveBeenCalled();
        expect(h.deps.showToast).not.toHaveBeenCalled();

        await h._onWebJoinApproved(approval());
        expect(h.saved()).toEqual([joinedRecord()]);
        expect(h.deps.podDeletePendingJoin).toHaveBeenCalledWith(ROOM, OWNER);
        expect(h.deps._clearJoinPendingBanner).toHaveBeenCalledWith(ROOM);
        expect(h.deps._registerRemoteRoom).toHaveBeenCalledOnce();
    });

    it('discards an unsolicited approval without fetching the owner pod', async () => {
        const h = joinerHarness();
        h.deps.podHasPendingJoin.mockResolvedValue(false);

        await h._onWebJoinApproved(approval());
        expect(h.deps.podReadRoomDescriptorAt).not.toHaveBeenCalled();
        expect(h.deps._updateJoinedRooms).not.toHaveBeenCalled();
        expect(h.deps._registerRemoteRoom).not.toHaveBeenCalled();
    });
});

describe('restored room sidebar and leave', () => {
    it('restores a joined room after a sidebar rebuild without duplicating membership', () => {
        const sidebar = new Map();
        const deps = {
            _remoteRooms: {},
            addRoomToSidebar: vi.fn((id, title) => sidebar.set(id, title)),
            _trackRoomSync: vi.fn(),
            webRoomAccess: { track: vi.fn() },
        };
        const { _registerRemoteRoom } = handlers(['_registerRemoteRoom'], deps);
        const info = joinedRecord();

        expect(_registerRemoteRoom(ROOM, info)).toBe(true);
        sidebar.clear(); // A later rooms event reconstructs the sidebar.
        expect(_registerRemoteRoom(ROOM, { ...info, title: 'Renamed friends' })).toBe(false);
        expect(sidebar.get(ROOM)).toBe('Renamed friends');
        expect(Object.keys(deps._remoteRooms)).toEqual([ROOM]);
        expect(deps._remoteRooms[ROOM].title).toBe('Renamed friends');
    });

    it('keeps a room visible and watched when leave cannot persist, then retries', async () => {
        let saved = [joinedRecord()];
        const deps = {
            _remoteRooms: { [ROOM]: joinedRecord() },
            webRoomSync: { untrack: vi.fn() },
            webRoomAccess: { untrack: vi.fn() },
            withoutJoinedRecord,
            _updateJoinedRooms: vi.fn(async (change) => { saved = change(saved); return true; }),
        };
        deps._updateJoinedRooms.mockResolvedValueOnce(false);
        const { _webLeaveRoom } = handlers(['_webLeaveRoom'], deps);

        expect(await _webLeaveRoom(ROOM)).toBe(false);
        expect(saved).toEqual([joinedRecord()]);
        expect(deps._remoteRooms[ROOM]).toBeDefined();
        expect(deps.webRoomSync.untrack).not.toHaveBeenCalled();

        expect(await _webLeaveRoom(ROOM)).toBe(true);
        expect(saved).toEqual([]);
        expect(deps._remoteRooms[ROOM]).toBeUndefined();
        expect(deps.webRoomSync.untrack).toHaveBeenCalledOnce();
        expect(await _webLeaveRoom(ROOM)).toBe(false);
    });
});
