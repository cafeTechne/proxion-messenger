import { describe, expect, it, vi } from 'vitest';
import { createWebJoin } from './webjoin.js';
import { createRoomApprovalNotice, createRoomApprovalStore, roomApprovalStatusKey } from './room-approvals.js';

const ROOT = 'https://owner.example/';
const OWNER = ROOT + 'profile/card#me';
const URL = ROOT + 'proxion/join-inbox/request-1';
const MSG = { kind: 'join_request', room_id: 'room-1', from_webid: 'https://joiner.example/#me' };
function storage() {
    const rows = new Map();
    return { getItem: key => rows.get(key) || null, setItem: (key, value) => rows.set(key, value), removeItem: key => rows.delete(key) };
}
function client(saved, { handle = vi.fn(async () => true), pod = {}, onStatusChange = vi.fn() } = {}) {
    const io = { podReadJoins: vi.fn(async () => [{ url: URL, msg: MSG }]),
        podDeleteJoin: vi.fn(async () => true), podEnsureJoinInbox: vi.fn(async () => ROOT + 'proxion/join-inbox/'), ...pod };
    const join = createWebJoin({ pod: io, getSelfWebId: () => OWNER, getSelfPodRoot: () => ROOT,
        peerPodRoot: () => 'https://joiner.example/', storage: saved, onJoinRequest: handle, onStatusChange });
    return { join, io, handle, onStatusChange };
}

describe('persistent room request status', () => {
    it('restores a failed request after reload and clears it when retry succeeds', async () => {
        const saved = storage();
        const first = client(saved, { handle: vi.fn(async () => false) });
        await first.join.drainOnce();
        expect(first.onStatusChange.mock.calls.at(-1)[0]).toMatchObject([{ kind: 'join_request', state: 'retry' }]);
        expect(first.io.podDeleteJoin).not.toHaveBeenCalled();
        first.join.stop();
        const next = client(saved);
        await next.join.start();
        expect(next.onStatusChange.mock.calls.some(([rows]) => rows[0]?.state === 'retry')).toBe(true);
        expect(next.handle).toHaveBeenCalledOnce();
        expect(next.onStatusChange.mock.calls.at(-1)[0]).toEqual([]);
        expect(saved.getItem(roomApprovalStatusKey(OWNER, ROOT))).toBe(null);
        next.join.stop();
    });

    it('retries failed inbox cleanup after reload without repeating an already handled decision', async () => {
        const saved = storage();
        const first = client(saved, { pod: { podDeleteJoin: vi.fn(async () => false) } });
        await first.join.drainOnce();
        await first.join.drainOnce();
        expect(first.handle).toHaveBeenCalledOnce();
        expect(first.io.podDeleteJoin).toHaveBeenCalledTimes(2);
        expect(first.onStatusChange.mock.calls.at(-1)[0][0].state).toBe('cleanup');
        first.join.stop();
        const next = client(saved);
        await next.join.drainOnce();
        expect(next.handle).not.toHaveBeenCalled();
        expect(next.io.podDeleteJoin).toHaveBeenCalledOnce();
        expect(saved.getItem(roomApprovalStatusKey(OWNER, ROOT))).toBe(null);
    });

    it('does not apply a cleanup receipt to different request contents at the same URL', async () => {
        const saved = storage();
        const first = client(saved, { pod: { podDeleteJoin: vi.fn(async () => false) } });
        await first.join.drainOnce(); first.join.stop();
        const next = client(saved, { pod: { podReadJoins: async () => [{ url: URL, msg: { ...MSG, room_id: 'another-room' } }] } });
        await next.join.drainOnce();
        expect(next.handle).toHaveBeenCalledOnce();
    });

    it.each([true, null, false])('only clears an absent cached request after confirmed deletion (%s)', async exists => {
        const saved = storage();
        const first = client(saved, { handle: vi.fn(async () => false) });
        await first.join.drainOnce(); first.join.stop();
        const next = client(saved, { pod: { podReadJoins: async () => [], podJoinExists: vi.fn(async () => exists) } });
        await next.join.start();
        const rows = next.onStatusChange.mock.calls.at(-1)[0];
        expect(rows.length).toBe(exists === false ? 0 : 1);
        expect(next.io.podDeleteJoin).not.toHaveBeenCalled();
        next.join.stop();
    });

    it('keeps recovery metadata isolated by account and pod root', () => {
        const saved = storage();
        const first = createRoomApprovalStore({ storage: saved, webId: OWNER, root: ROOT });
        first.put(URL, MSG, 'cleanup');
        expect(createRoomApprovalStore({ storage: saved, webId: 'https://other/#me', root: ROOT }).list()).toEqual([]);
        expect(createRoomApprovalStore({ storage: saved, webId: OWNER, root: ROOT + 'moved/' }).list()).toEqual([]);
        expect(createRoomApprovalStore({ storage: saved, webId: OWNER, root: ROOT }).get(URL, MSG)?.state).toBe('cleanup');
    });

    it('rejects cached recovery entries outside the owner inbox', () => {
        const saved = storage();
        const store = createRoomApprovalStore({ storage: saved, webId: OWNER, root: ROOT });
        store.put(URL, MSG, 'cleanup');
        const raw = JSON.parse(saved.getItem(roomApprovalStatusKey(OWNER, ROOT)));
        raw[0].url = 'https://stranger.example/proxion/join-inbox/request';
        saved.setItem(roomApprovalStatusKey(OWNER, ROOT), JSON.stringify(raw));
        expect(createRoomApprovalStore({ storage: saved, webId: OWNER, root: ROOT }).list()).toEqual([]);
    });

    it('matches approval cleanup to its owner rather than an unrelated requester field', () => {
        const store = createRoomApprovalStore({ storage: storage(), webId: OWNER, root: ROOT });
        const approval = { kind: 'join_approved', room_id: 'room-1', owner_webid: 'https://original-owner/#me' };
        store.put(URL, approval, 'cleanup');
        expect(store.get(URL, { ...approval, owner_webid: 'https://another-owner/#me', from_webid: approval.owner_webid })).toBe(null);
    });
});

describe('owner request notice', () => {
    it('offers retry and prevents an old retry completion from changing the next account notice', async () => {
        let releaseOld, releaseNew;
        const text = { textContent: '' }, button = { disabled: false, onclick: null };
        const element = { classList: { toggle: vi.fn() }, querySelector: selector => selector === 'button' ? button : text };
        const show = createRoomApprovalNotice({ element, translate: (key, { count }) => key + ':' + count });
        const rows = [{ kind: 'join_request', state: 'retry' }];
        show(rows, () => new Promise(resolve => { releaseOld = resolve; }));
        expect(text.textContent).toBe('join.ownerRetry:1');
        const old = button.onclick();
        expect(button.disabled).toBe(true);
        show([], null);
        expect(button.onclick).toBe(null);
        show([{ kind: 'join_request', state: 'cleanup' }], () => new Promise(resolve => { releaseNew = resolve; }));
        const current = button.onclick();
        releaseOld(); await old;
        expect(button.disabled).toBe(true);
        releaseNew(); await current;
        expect(button.disabled).toBe(false);
        expect(text.textContent).toBe('join.ownerCleanup:1');
    });
});

it('publishes and persists pending requests before an owner answers the prompt', async () => {
    const saved = storage(); let answer;
    const h = client(saved, { handle: () => new Promise(resolve => { answer = resolve; }) });
    const draining = h.join.drainOnce();
    await vi.waitFor(() => expect(h.onStatusChange).toHaveBeenCalledWith(expect.arrayContaining([
        expect.objectContaining({ kind: 'join_request', state: 'pending' }),
    ]), expect.any(Function)));
    expect(createRoomApprovalStore({ storage: saved, webId: OWNER, root: ROOT }).list()[0].state).toBe('pending');
    expect(h.io.podDeleteJoin).not.toHaveBeenCalled();
    answer(false); await draining;
    expect(h.onStatusChange.mock.calls.at(-1)[0][0].state).toBe('retry');
});

it('offers Review while a request is waiting for an owner decision', async () => {
    const text = { textContent: '' }, button = { disabled: false }, review = vi.fn(), retry = vi.fn(async () => {});
    const element = { classList: { toggle: vi.fn() }, querySelector: selector => selector === 'button' ? button : text };
    const show = createRoomApprovalNotice({ element, translate: key => key, review });
    show([{ kind: 'join_request', state: 'pending' }], retry);
    expect(text.textContent).toBe('join.ownerPending');
    expect(button.textContent).toBe('join.reviewRequests');
    await button.onclick();
    expect(review).toHaveBeenCalledOnce(); expect(retry).toHaveBeenCalledOnce();
    expect(button.disabled).toBe(false);
});
