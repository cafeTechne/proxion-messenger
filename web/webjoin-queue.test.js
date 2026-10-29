import { describe, it, expect, vi } from 'vitest';
import { createWebJoin, makeInvite, parseInvite } from './webjoin.js';

describe('invite links from a URL with a query or fragment', () => {
    it('builds from origin + path, so a #fragment never lands in the token', () => {
        const link = makeInvite('room-1', 'https://alice.example/profile/card#me', 'https://x.example/app/#settings');
        expect(link.startsWith('https://x.example/app/?join=')).toBe(true);
        expect(parseInvite(link)).toEqual({ roomId: 'room-1', ownerWebId: 'https://alice.example/profile/card#me' });
    });
    it('can carry mode=web', () => {
        const link = makeInvite('r', 'https://a.example/#me', 'https://x.example/?mode=web&x=1', { webMode: true });
        const u = new URL(link);
        expect(u.searchParams.get('mode')).toBe('web');
        expect(u.searchParams.get('x')).toBe(null);
        expect(parseInvite(link).roomId).toBe('r');
    });
});

function base(joins, handlers) {
    const deleted = [];
    const dropped = [];
    const pod = {
        podEnsureJoinInbox: vi.fn(async () => 'inbox'),
        podDropJoin: vi.fn(async (root, msg) => { dropped.push({ root, msg }); return true; }),
        podReadJoins: vi.fn(async () => joins),
        podDeleteJoin: vi.fn(async (url) => { deleted.push(url); return true; }),
    };
    const join = createWebJoin({
        pod, notify: { watchResource: vi.fn(() => () => {}) },
        getSelfWebId: () => 'https://me.example/profile/card#me', getDisplayName: () => 'Me',
        getSelfPodRoot: () => 'https://me.example/', peerPodRoot: (w) => w.replace(/profile\/card#me$/, ''),
        ...handlers,
    });
    return { join, deleted, dropped };
}

describe('createWebJoin prompts and declines', () => {
    it('does not prompt twice for one request when drains overlap', async () => {
        let release;
        const asked = [];
        const { join, deleted } = base(
            [{ url: 'u1', msg: { kind: 'join_request', room_id: 'r', from_webid: 'https://bob' } }],
            { onJoinRequest: (m) => { asked.push(m); return new Promise((r) => { release = r; }); } },
        );
        const d1 = join.drainOnce();
        await new Promise((r) => setTimeout(r, 0));
        await join.drainOnce();   // a notification drain while the prompt is open
        expect(asked).toHaveLength(1);
        expect(deleted).toEqual([]);   // not deleted before the owner answers
        release(true);
        await d1;
        expect(deleted).toEqual(['u1']);
        await join.drainOnce();   // a stale listing still naming it
        expect(asked).toHaveLength(1);
    });

    it('handles every request, not only the last one', async () => {
        const asked = [];
        const { join, deleted } = base([
            { url: 'u1', msg: { kind: 'join_request', room_id: 'r', from_webid: 'https://bob' } },
            { url: 'u2', msg: { kind: 'join_request', room_id: 'r', from_webid: 'https://carol' } },
        ], { onJoinRequest: async (m) => { asked.push(m.from_webid); return true; } });
        await join.drainOnce();
        expect(asked).toEqual(['https://bob', 'https://carol']);
        expect(deleted.sort()).toEqual(['u1', 'u2']);
    });

    it('keeps an unanswered request for next time', async () => {
        const { join, deleted } = base(
            [{ url: 'u1', msg: { kind: 'join_request', room_id: 'r', from_webid: 'https://bob' } }],
            { onJoinRequest: async () => false },
        );
        await join.drainOnce();
        expect(deleted).toEqual([]);
    });

    it('sendDecline drops a join_declined to the joiner, which is routed to onDeclined', async () => {
        const { join, dropped } = base([], {});
        await join.sendDecline('https://bob.example/profile/card#me', 'r');
        expect(dropped[0]).toEqual({ root: 'https://bob.example/', msg: { kind: 'join_declined', room_id: 'r', owner_webid: 'https://me.example/profile/card#me' } });
        const declined = [];
        const j2 = base([{ url: 'd1', msg: dropped[0].msg }], { onDeclined: (m) => { declined.push(m); } });
        await j2.join.drainOnce();
        expect(declined).toHaveLength(1);
        expect(j2.deleted).toEqual(['d1']);
    });
});
