// roomdesc.test.js — PLAN_ROUND_71 B1: the canonical room descriptor. Pure
// build/parse/normalise; the live write+read round trip is in
// podcanonical-roomdesc.test.js.
import { describe, it, expect } from 'vitest';
import {
    buildRoomDescriptor, normalizeMembers, withMembers, parseRoomDescriptor,
    ROOM_DESC_VERSION,
} from './roomdesc.js';

const OWNER = 'https://me.pod/profile/card#me';
const BOB = 'https://bob.pod/profile/card#me';

describe('normalizeMembers', () => {
    it('always includes the owner with role owner, even if absent from the list', () => {
        const m = normalizeMembers([{ webid: BOB, role: 'member' }], OWNER);
        expect(m.find(x => x.webid === OWNER)).toEqual({ webid: OWNER, role: 'owner', banned: false });
    });

    it('forces the owner to role owner even if the list says otherwise', () => {
        const m = normalizeMembers([{ webid: OWNER, role: 'member' }], OWNER);
        expect(m.find(x => x.webid === OWNER).role).toBe('owner');
    });

    it('dedups by webid (first wins) and coerces an unknown role to member', () => {
        const m = normalizeMembers([{ webid: BOB, role: 'wizard' }, { webid: BOB, role: 'admin' }], OWNER);
        const bob = m.filter(x => x.webid === BOB);
        expect(bob).toHaveLength(1);
        expect(bob[0].role).toBe('member');   // first entry's invalid "wizard" -> member
    });

    it('accepts bare webid strings', () => {
        const m = normalizeMembers([BOB], OWNER);
        expect(m.find(x => x.webid === BOB)).toEqual({ webid: BOB, role: 'member', banned: false });
    });
});

describe('buildRoomDescriptor', () => {
    it('produces a versioned descriptor with owner, members, and timestamps', () => {
        const d = buildRoomDescriptor({ roomId: 'general', title: 'General', owner: OWNER, members: [BOB] });
        expect(d['px:type']).toBe('RoomDescriptor');
        expect(d['px:version']).toBe(ROOM_DESC_VERSION);
        expect(d.room_id).toBe('general');
        expect(d.owner).toBe(OWNER);
        expect(d.members.map(x => x.webid)).toContain(BOB);
        expect(d.members.find(x => x.webid === OWNER).role).toBe('owner');
        expect(typeof d.created).toBe('string');
        expect(typeof d.updated).toBe('string');
    });
});

describe('withMembers', () => {
    it('replaces membership, keeps owner + id, bumps updated', () => {
        const d = buildRoomDescriptor({ roomId: 'general', title: 'G', owner: OWNER, created: '2026-01-01T00:00:00Z', updated: '2026-01-01T00:00:00Z' });
        const d2 = withMembers(d, [{ webid: BOB }]);
        expect(d2.room_id).toBe('general');
        expect(d2.owner).toBe(OWNER);
        expect(d2.members.map(x => x.webid).sort()).toEqual([BOB, OWNER].sort());
        expect(d2.updated).not.toBe(d.updated);
        expect(d2.created).toBe(d.created);
    });
});

describe('parseRoomDescriptor', () => {
    it('round-trips a built descriptor', () => {
        const d = buildRoomDescriptor({ roomId: 'general', title: 'G', owner: OWNER, members: [BOB], longChat: 'https://me.pod/proxion/rooms/general/' });
        const p = parseRoomDescriptor(JSON.parse(JSON.stringify(d)));
        expect(p.room_id).toBe('general');
        expect(p.owner).toBe(OWNER);
        expect(p.long_chat).toBe('https://me.pod/proxion/rooms/general/');
        expect(p.members.map(x => x.webid).sort()).toEqual([BOB, OWNER].sort());
    });

    it('rejects a doc missing room_id or owner', () => {
        expect(parseRoomDescriptor({ owner: OWNER })).toBe(null);
        expect(parseRoomDescriptor({ room_id: 'x' })).toBe(null);
        expect(parseRoomDescriptor(null)).toBe(null);
    });

    it('tolerates legacy extra fields (name/code) alongside descriptor fields', () => {
        const p = parseRoomDescriptor({ room_id: 'general', owner: OWNER, name: 'G', code: 'ABC', members: [BOB] });
        expect(p.room_id).toBe('general');
        expect(p.members.map(x => x.webid)).toContain(BOB);
    });
});
