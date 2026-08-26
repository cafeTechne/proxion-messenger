import { describe, it, expect, vi } from 'vitest';
import { createWebJoin, makeInvite, parseInvite } from './webjoin.js';

describe('invite encoding', () => {
    it('round-trips a room id and owner WebID through a link', () => {
        const link = makeInvite('room-1', 'https://alice.example/profile/card#me', 'https://app.example/app/');
        expect(link).toContain('?join=');
        expect(parseInvite(link)).toEqual({ roomId: 'room-1', ownerWebId: 'https://alice.example/profile/card#me' });
    });
    it('parses a raw token too', () => {
        expect(parseInvite('r2~https://o.example/#me')).toEqual({ roomId: 'r2', ownerWebId: 'https://o.example/#me' });
    });
    it('rejects malformed or non-https invites', () => {
        expect(parseInvite('')).toBe(null);
        expect(parseInvite('nosep')).toBe(null);
        expect(parseInvite('r~http://insecure')).toBe(null);
    });
});

function harness({ joins = [] } = {}) {
    const dropped = [];
    const deleted = [];
    const requests = [];
    const approvals = [];
    const pod = {
        podEnsureJoinInbox: vi.fn(async () => 'https://me/proxion/join-inbox/'),
        podDropJoin: vi.fn(async (root, msg) => { dropped.push({ root, msg }); return true; }),
        podReadJoins: vi.fn(async () => joins),
        podDeleteJoin: vi.fn(async (url) => { deleted.push(url); return true; }),
    };
    const notify = { watchResource: vi.fn(() => () => {}) };
    const join = createWebJoin({
        pod, notify,
        getSelfWebId: () => 'https://me/profile/card#me', getDisplayName: () => 'Me',
        getSelfPodRoot: () => 'https://me.example/',
        peerPodRoot: (w) => w.replace(/profile\/card#me$/, ''),
        onJoinRequest: (m) => requests.push(m),
        onApproved: (m) => approvals.push(m),
    });
    return { join, pod, notify, dropped, deleted, requests, approvals };
}

describe('createWebJoin', () => {
    it('requestJoin drops a join_request to the owner', async () => {
        const { join, dropped } = harness();
        expect(await join.requestJoin('room-1', 'https://alice.example/profile/card#me')).toBe(true);
        expect(dropped[0].root).toBe('https://alice.example/');
        expect(dropped[0].msg).toMatchObject({ kind: 'join_request', room_id: 'room-1', from_webid: 'https://me/profile/card#me' });
    });

    it('sendApproval drops a join_approved carrying where the room lives', async () => {
        const { join, dropped } = harness();
        await join.sendApproval('https://bob.example/profile/card#me', 'room-1', 'General');
        expect(dropped[0].root).toBe('https://bob.example/');
        expect(dropped[0].msg).toMatchObject({
            kind: 'join_approved', room_id: 'room-1', title: 'General',
            owner_webid: 'https://me/profile/card#me', owner_pod_root: 'https://me.example/',
        });
    });

    it('drain routes requests to the owner callback and deletes them', async () => {
        const { join, requests, deleted } = harness({ joins: [
            { url: 'u1', msg: { kind: 'join_request', room_id: 'r', from_webid: 'https://bob' } },
        ] });
        await join.drainOnce();
        expect(requests).toHaveLength(1);
        expect(requests[0].from_webid).toBe('https://bob');
        expect(deleted).toEqual(['u1']);
    });

    it('drain routes approvals to the joiner callback', async () => {
        const { join, approvals } = harness({ joins: [
            { url: 'u2', msg: { kind: 'join_approved', room_id: 'r', owner_webid: 'https://alice', owner_pod_root: 'https://alice.example/' } },
        ] });
        await join.drainOnce();
        expect(approvals[0]).toMatchObject({ room_id: 'r', owner_pod_root: 'https://alice.example/' });
    });

    it('start ensures the inbox, drains, and subscribes', async () => {
        const { join, pod, notify } = harness();
        await join.start();
        expect(pod.podEnsureJoinInbox).toHaveBeenCalled();
        expect(notify.watchResource).toHaveBeenCalledWith('https://me/proxion/join-inbox/', expect.any(Function));
    });
});
