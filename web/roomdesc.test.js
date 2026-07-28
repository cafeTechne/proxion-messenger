// roomdesc.test.js — PLAN_ROUND_71 B1: the canonical room descriptor. Pure
// build/parse/normalise; the live write+read round trip is in
// podcanonical-roomdesc.test.js.
import { describe, it, expect } from 'vitest';
import {
    buildRoomDescriptor, normalizeMembers, withMembers, parseRoomDescriptor,
    descriptorSigningBytes, ROOM_DESC_VERSION,
} from './roomdesc.js';

const _toHex = (u8) => Array.from(u8).map((b) => b.toString(16).padStart(2, '0')).join('');

describe('descriptorSigningBytes (B3: must match Python room_descriptor.canonical_bytes)', () => {
    const desc = {
        room_id: 'room-abc123',
        owner: 'https://me.pod/profile/card#me',
        created: '2026-07-26T09:00:00.000Z',
        long_chat: 'https://me.pod/proxion/rooms/room-abc123/',
        members: [
            { webid: 'https://bob.pod/#me', role: 'member' },
            { webid: 'https://me.pod/profile/card#me', role: 'owner' },
        ],
    };
    // Vector produced by the Python canonical_bytes for the SAME descriptor. If the
    // two encoders drift, a browser-signed descriptor stops verifying on the gateway.
    const EXPECTED =
        '001a70726f78696f6e2d726f6f6d2d64657363726970746f722d76317c000b726f6f6d2d61' +
        '6263313233' + '7c001e68747470733a2f2f6d652e706f642f70726f66696c652f63617264236d65' +
        '7c0018323032362d30372d32365430393a30303a30302e3030305a' +
        '7c003f68747470733a2f2f626f622e706f642f236d651f6d656d6265721e68747470733a2f2f6d' +
        '652e706f642f70726f66696c652f63617264236d651f6f776e6572';

    it('produces the exact cross-language byte vector', () => {
        expect(_toHex(descriptorSigningBytes(desc))).toBe(EXPECTED);
    });

    it('excludes long_chat and updated (filled/changed after signing)', () => {
        expect(_toHex(descriptorSigningBytes({ ...desc, long_chat: 'X', updated: 'Y' }))).toBe(EXPECTED);
    });

    it('is stable regardless of member order (canonical sort)', () => {
        expect(_toHex(descriptorSigningBytes({ ...desc, members: [...desc.members].reverse() }))).toBe(EXPECTED);
    });
});

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
