// Coordinate owner membership changes against versioned pod descriptors. Each
// tab has its own queue; HTTP preconditions protect updates across devices.
import { closeRoomDescriptor } from './roomdesc.js';
export function isStrongEtag(value) {
    return typeof value === 'string' && value.length <= 512
        && /^"[\x21\x23-\x7e\x80-\xff]*"$/.test(value);
}

export function createOwnerMembership({
    read, write, apply, getOwnerId, signDescriptor = async (descriptor) => descriptor,
    maxRetries = 5,
}) {
    const pending = new Map();
    const failure = (reason) => ({ ok: false, reason });

    async function updateOnce(roomId, change, owner, closing = false) {
        const isCurrent = () => !!owner && getOwnerId() === owner;
        const usable = (snapshot) => snapshot?.ok && snapshot.descriptor
            && snapshot.descriptor.room_id === roomId
            && snapshot.descriptor.owner === owner && isStrongEtag(snapshot.etag);
        if (!isCurrent()) return failure('account_changed');

        let committed = false;
        for (let attempt = 0; attempt < maxRetries; attempt++) {
            if (!isCurrent()) return failure('account_changed');
            const snapshot = await read(roomId);
            if (!isCurrent()) return failure('account_changed');
            if (!usable(snapshot)) return failure('descriptor_unavailable');
            if (snapshot.descriptor.closed && !closing) { committed = true; break; }
            const changed = change(snapshot.descriptor);
            if (changed == null) return { ok: true, skipped: true, descriptor: snapshot.descriptor };
            if (changed.room_id !== roomId || changed.owner !== owner) return failure('invalid_change');
            if (!closing && changed.closed !== snapshot.descriptor.closed) return failure('invalid_change');
            if (changed === snapshot.descriptor) { committed = true; break; }
            const signed = await signDescriptor(changed);
            if (!isCurrent()) return failure('account_changed');
            const saved = await write(signed, snapshot);
            if (!isCurrent()) return failure('account_changed');
            if (saved?.conflict) continue;
            if (!saved?.ok) return failure('write_failed');
            committed = true;
            break;
        }
        if (!committed) return failure('conflict');

        // An older device can finish an ACL write after a newer descriptor was
        // committed. Re-read after side effects and repair from the latest
        // descriptor before acknowledging either approval or removal.
        for (let attempt = 0; attempt < maxRetries; attempt++) {
            if (!isCurrent()) return failure('account_changed');
            const snapshot = await read(roomId);
            if (!isCurrent()) return failure('account_changed');
            if (!usable(snapshot)) return failure('descriptor_unavailable');
            let applied = false;
            const accessVersion = { etag: snapshot.etag, check: async () => {
                if (!isCurrent()) return false;
                const current = await read(roomId);
                return isCurrent() && usable(current) && current.etag === snapshot.etag;
            } };
            try { applied = await apply(snapshot.descriptor.closed ? closeRoomDescriptor(snapshot.descriptor) : snapshot.descriptor, isCurrent, accessVersion); } catch { /* verify any partial side effects below */ }
            if (!isCurrent()) return failure('account_changed');
            const latest = await read(roomId);
            if (!isCurrent()) return failure('account_changed');
            if (!usable(latest)) return failure('descriptor_unavailable');
            if (latest.etag !== snapshot.etag) continue;
            if (!applied) return failure('access_update_failed');
            if (latest.descriptor.closed && !closing) return failure('room_closed');
            return { ok: true, descriptor: latest.descriptor };
        }
        return failure('conflict');
    }

    function enqueue(roomId, operation) {
        const owner = getOwnerId();
        const key = String(owner) + '\n' + roomId;
        const previous = pending.get(key) || Promise.resolve();
        const result = previous.catch(() => {}).then(() => operation(owner))
            .catch(() => failure('unavailable'));
        pending.set(key, result);
        result.finally(() => { if (pending.get(key) === result) pending.delete(key); }).catch(() => {});
        return result;
    }

    function update(roomId, change) {
        return enqueue(roomId, owner => updateOnce(roomId, change, owner));
    }

    function remove(roomId, unlist) {
        return enqueue(roomId, async owner => {
            const closed = await updateOnce(roomId, d => d.closed ? d : closeRoomDescriptor(d), owner, true);
            if (!closed.ok) return closed;
            if (getOwnerId() !== owner) return failure('account_changed');
            if (!(await unlist())) return failure('unlist_failed');
            if (getOwnerId() !== owner) return failure('account_changed');
            return updateOnce(roomId, d => d.deletion_complete ? d : closeRoomDescriptor(d, true), owner, true);
        });
    }

    return { update, remove };
}
