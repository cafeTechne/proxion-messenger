import { describe, it, expect, vi } from 'vitest';
import { createOwnerMembership, isStrongEtag } from './room-membership.js';
import { buildRoomDescriptor, withMembers, closeRoomDescriptor } from './roomdesc.js';

const OWNER = 'https://owner.example/profile/card#me';
const ALICE = 'https://alice.example/profile/card#me';
const BOB = 'https://bob.example/profile/card#me';
const ROOM = 'room-r130';
const initial = (members = []) => buildRoomDescriptor({ roomId: ROOM, title: 'Room', owner: OWNER,
    members: [{ webid: OWNER, role: 'owner' }, ...members.map((webid) => ({ webid, role: 'member' }))] });
const add = (webid) => (descriptor) => withMembers(descriptor, [...descriptor.members, { webid, role: 'member' }]);
const names = (descriptor) => descriptor.members.filter((m) => !m.banned).map((m) => m.webid);
const deferred = () => {
    let resolve;
    const promise = new Promise((r) => { resolve = r; });
    return { promise, resolve };
};

function store(descriptor = initial()) {
    let current = structuredClone(descriptor), version = 0;
    const state = { acl: names(current), members: names(current) };
    const read = vi.fn(async () => ({ ok: true, descriptor: structuredClone(current), etag: `"v${version}"` }));
    const write = vi.fn(async (updated, snapshot) => {
        if (snapshot.etag !== `"v${version}"`) return { ok: false, conflict: true, status: 412 };
        current = structuredClone(updated);
        version++;
        return { ok: true, status: 200 };
    });
    const apply = vi.fn(async (latest) => {
        state.acl = names(latest);
        state.members = names(latest);
        return true;
    });
    return { state, read, write, apply, descriptor: () => structuredClone(current),
        client: (extra = {}) => createOwnerMembership({ read, write, apply, getOwnerId: () => OWNER, ...extra }) };
}

describe('owner membership across independent devices', () => {
    it('does not commit an old operation after the same account reconnects', async () => {
        const s = store();
        let session = 1, release;
        const signing = new Promise(resolve => { release = resolve; });
        const client = s.client({ getSessionId: () => session, signDescriptor: () => signing });
        const updating = client.update(ROOM, add(ALICE));
        await vi.waitFor(() => expect(s.read).toHaveBeenCalled());
        session++;
        release(add(ALICE)(s.descriptor()));
        expect(await updating).toEqual({ ok: false, reason: 'account_changed' });
        expect(s.write).not.toHaveBeenCalled();
    });
    it('reapplies a conflicting approval without losing the other approved member', async () => {
        const s = store();
        const bothRead = deferred();
        let firstReads = 0;
        const read = async () => {
            const snapshot = await s.read();
            if (++firstReads <= 2) {
                if (firstReads === 2) bothRead.resolve();
                await bothRead.promise;
            }
            return snapshot;
        };
        const result = await Promise.all([
            s.client({ read }).update(ROOM, add(ALICE)),
            s.client({ read }).update(ROOM, add(BOB)),
        ]);
        expect(result.every((r) => r.ok)).toBe(true);
        expect(names(s.descriptor())).toEqual(expect.arrayContaining([OWNER, ALICE, BOB]));
        expect(s.state.acl).toEqual(names(s.descriptor()));
        expect(s.state.members).toEqual(names(s.descriptor()));
        expect(s.write).toHaveBeenCalledTimes(3);
    });

    it('repairs a delayed old ACL after another device approves a different member', async () => {
        const s = store();
        const applying = deferred(), release = deferred();
        let first = true;
        const apply = async (descriptor) => {
            if (first) { first = false; applying.resolve(); await release.promise; }
            return s.apply(descriptor);
        };
        const delayed = s.client({ apply }).update(ROOM, add(ALICE));
        await applying.promise;
        expect((await s.client().update(ROOM, add(BOB))).ok).toBe(true);
        release.resolve();
        expect((await delayed).ok).toBe(true);
        expect(s.state.acl).toEqual(expect.arrayContaining([OWNER, ALICE, BOB]));
        expect(s.state.members).toEqual(names(s.descriptor()));
    });

    it('never leaves a removed member authorized after a delayed partial approval repair', async () => {
        const s = store(initial([ALICE]));
        const applying = deferred(), release = deferred();
        let first = true;
        const delayed = s.client({ apply: async (descriptor) => {
            if (first) {
                first = false; applying.resolve(); await release.promise;
                await s.apply(descriptor);
                return false; // the old member-list write failed too
            }
            return s.apply(descriptor);
        } }).update(ROOM, (descriptor) => descriptor);
        await applying.promise;
        const removed = await s.client().update(ROOM,
            (descriptor) => withMembers(descriptor, descriptor.members.filter((m) => m.webid !== ALICE)));
        expect(removed.ok).toBe(true);
        release.resolve();
        const repair = await delayed;
        expect(repair.ok).toBe(true);
        expect(names(repair.descriptor)).not.toContain(ALICE);
        expect(s.state.acl).not.toContain(ALICE);
        expect(s.state.members).not.toContain(ALICE);
    });

    it('rechecks a ban when a conflicting approval is retried', async () => {
        const s = store();
        let first = true;
        const write = vi.fn(async (descriptor, snapshot) => {
            if (first) {
                first = false;
                const latest = await s.read();
                await s.write(withMembers(latest.descriptor,
                    [...latest.descriptor.members, { webid: ALICE, role: 'member', banned: true }]), latest);
            }
            return s.write(descriptor, snapshot);
        });
        const result = await s.client({ write }).update(ROOM, (descriptor) => {
            if (descriptor.members.some((m) => m.webid === ALICE && m.banned)) return null;
            return add(ALICE)(descriptor);
        });
        expect(result).toMatchObject({ ok: true, skipped: true });
        expect(s.apply).not.toHaveBeenCalled();
        expect(s.descriptor().members.find((m) => m.webid === ALICE).banned).toBe(true);
    });

    it('retains a failed access update for retry and repairs a persisted member without another descriptor write', async () => {
        const s = store();
        s.apply.mockResolvedValueOnce(false);
        const client = s.client();
        expect(await client.update(ROOM, add(ALICE))).toMatchObject({ ok: false, reason: 'access_update_failed' });
        expect(names(s.descriptor())).toContain(ALICE);
        expect((await client.update(ROOM, (descriptor) => descriptor)).ok).toBe(true);
        expect(s.write).toHaveBeenCalledOnce();
        expect(s.state.acl).toContain(ALICE);
    });

    it('bounds repeated write conflicts without applying uncommitted membership', async () => {
        const s = store();
        const write = vi.fn(async () => ({ ok: false, status: 412, conflict: true }));
        expect(await s.client({ write, maxRetries: 3 }).update(ROOM, add(ALICE)))
            .toEqual({ ok: false, reason: 'conflict' });
        expect(write).toHaveBeenCalledTimes(3);
        expect(s.apply).not.toHaveBeenCalled();
        expect(names(s.descriptor())).not.toContain(ALICE);
    });

    it.each([null, 'W/"weak"', 'not-a-tag'])('refuses a blind descriptor mutation without a strong version (%s)', async (etag) => {
        const s = store();
        const read = vi.fn(async () => ({ ok: true, descriptor: initial(), etag }));
        expect((await s.client({ read }).update(ROOM, add(ALICE))).ok).toBe(false);
        expect(s.write).not.toHaveBeenCalled();
        expect(s.apply).not.toHaveBeenCalled();
    });

    it('stops before writing if the account changes while signing', async () => {
        const s = store();
        let owner = OWNER;
        const result = await s.client({ getOwnerId: () => owner,
            signDescriptor: async (descriptor) => { owner = BOB; return descriptor; } }).update(ROOM, add(ALICE));
        expect(result).toEqual({ ok: false, reason: 'account_changed' });
        expect(s.write).not.toHaveBeenCalled();
        expect(s.apply).not.toHaveBeenCalled();
    });

    it('passes an account guard into access writes and never acknowledges an account change', async () => {
        const s = store();
        let owner = OWNER;
        const apply = vi.fn(async (_descriptor, isCurrent) => {
            expect(isCurrent()).toBe(true);
            owner = BOB;
            expect(isCurrent()).toBe(false);
            return false;
        });
        expect(await s.client({ getOwnerId: () => owner, apply }).update(ROOM, add(ALICE)))
            .toEqual({ ok: false, reason: 'account_changed' });
    });

    it('never accepts membership/ownership edits for a different room or owner', async () => {
        const s = store();
        expect((await s.client().update(ROOM, (descriptor) => ({ ...descriptor, room_id: 'different' }))).ok).toBe(false);
        expect((await s.client().update(ROOM, (descriptor) => ({ ...descriptor, owner: BOB }))).ok).toBe(false);
        expect(s.write).not.toHaveBeenCalled();
    });
});

describe('strong HTTP descriptor versions', () => {
    it('accepts opaque quoted tags and rejects weak or unsafe headers', () => {
        expect(isStrongEtag('"version-1"')).toBe(true);
        expect(isStrongEtag('""')).toBe(true);
        for (const tag of ['W/"weak"', 'unquoted', '"new\r\nIf-Match:*"', '"sp ace"']) expect(isStrongEtag(tag)).toBe(false);
    });
});

describe('durable room deletion', () => {
    it('revokes access before unlisting and retains an owner-only closed descriptor', async () => {
        const s = store(initial([ALICE]));
        const unlist = vi.fn(async () => {
            expect(s.descriptor().closed).toBe(true);
            expect(s.state.acl).toEqual([OWNER]);
            expect(s.state.members).toEqual([OWNER]);
            return true;
        });
        expect((await s.client().remove(ROOM, unlist)).ok).toBe(true);
        expect(unlist).toHaveBeenCalledOnce();
        expect(s.descriptor()).toMatchObject({ closed: true, deletion_complete: true, 'px:version': 2 });
        expect(names(s.descriptor())).toEqual([OWNER]);
    });

    it.each(['descriptor', 'acl', 'unlist', 'completion'])('recovers a failed %s step with a new owner client', async stage => {
        const s = store(initial([ALICE]));
        const unlist = vi.fn(async () => true);
        const write = s.write.getMockImplementation();
        if (stage === 'descriptor') s.write.mockResolvedValueOnce({ ok: false });
        if (stage === 'acl') s.apply.mockResolvedValueOnce(false);
        if (stage === 'unlist') unlist.mockResolvedValueOnce(false);
        if (stage === 'completion') {
            s.write.mockImplementation(async (d, version) => d.deletion_complete ? { ok: false } : write(d, version));
        }
        expect((await s.client().remove(ROOM, unlist)).ok).toBe(false);
        if (stage !== 'descriptor') expect(s.descriptor().closed).toBe(true);
        expect(s.descriptor().deletion_complete).not.toBe(true);
        if (stage === 'descriptor' || stage === 'acl') expect(unlist).not.toHaveBeenCalled();
        s.write.mockImplementation(write);
        expect((await s.client().remove(ROOM, unlist)).ok).toBe(true);
        expect(s.descriptor().deletion_complete).toBe(true);
    });

    it('repairs an approval ACL that finishes after another device closes the room', async () => {
        const s = store();
        const applying = deferred(), release = deferred();
        let first = true;
        const apply = async d => {
            if (first) { first = false; applying.resolve(); await release.promise; }
            return s.apply(d);
        };
        const approval = s.client({ apply }).update(ROOM, add(ALICE));
        await applying.promise;
        expect((await s.client().remove(ROOM, async () => true)).ok).toBe(true);
        release.resolve();
        expect(await approval).toEqual({ ok: false, reason: 'room_closed' });
        expect(s.state.acl).toEqual([OWNER]);
        expect(s.state.members).toEqual([OWNER]);
        expect(s.descriptor().deletion_complete).toBe(true);
    });

    it('does not invoke an approval change on a closed room and repairs stale grants', async () => {
        const s = store(closeRoomDescriptor(initial([ALICE]), true));
        s.state.acl = [OWNER, ALICE];
        const change = vi.fn(add(BOB));
        expect(await s.client().update(ROOM, change)).toEqual({ ok: false, reason: 'room_closed' });
        expect(change).not.toHaveBeenCalled();
        expect(s.state.acl).toEqual([OWNER]);
    });

    it('refuses an approval whose descriptor snapshot predates closure', async () => {
        const s = store();
        const captured = deferred(), release = deferred();
        let first = true;
        const read = async () => {
            const snapshot = await s.read();
            if (first) { first = false; captured.resolve(); await release.promise; }
            return snapshot;
        };
        const approval = s.client({ read }).update(ROOM, add(ALICE));
        await captured.promise;
        expect((await s.client().remove(ROOM, async () => true)).ok).toBe(true);
        release.resolve();
        expect(await approval).toEqual({ ok: false, reason: 'room_closed' });
        expect(names(s.descriptor())).toEqual([OWNER]);
        expect(s.state.acl).toEqual([OWNER]);
    });

    it('stops cleanup completion if the account changes while unlisting', async () => {
        const s = store(initial([ALICE]));
        let owner = OWNER;
        const result = await s.client({ getOwnerId: () => owner }).remove(ROOM, async () => {
            owner = BOB;
            return true;
        });
        expect(result).toEqual({ ok: false, reason: 'account_changed' });
        expect(s.descriptor()).toMatchObject({ closed: true, deletion_complete: false });
        expect(s.state.acl).toEqual([OWNER]);
    });
});
