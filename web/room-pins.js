// Durable owner-signed pin discovery. The snapshot contains references, never
// authoritative message text. Conditional replacement bounds its size and
// preserves concurrent owner-device changes without an ever-growing event log.
import { signRoomPins, verifyRoomPins } from './dmsig.js';
import { messageIriAt } from './longchat.js';
import { isStrongEtag } from './room-membership.js';

export const MAX_ROOM_PINS = 50;
const MAX_BYTES = 65536;
const ID = /^[\w-]{1,128}$/;
const iso = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;

export function pinTarget(container, target) {
    if (typeof target !== 'string' || !target.startsWith(container)) return null;
    const match = /^(\d{4})\/(\d{2})\/(\d{2})\/chat\.ttl#([\w-]{1,128})$/.exec(target.slice(container.length));
    if (!match) return null;
    const date = `${match[1]}-${match[2]}-${match[3]}T00:00:00.000Z`;
    if (!iso(date)) return null;
    return { target, message_id: match[4], date };
}

export function normalizePinSnapshot(doc, container, owner) {
    if (!doc || doc.v !== 1 || doc.index !== container + 'pins.json' || doc.owner !== owner
        || typeof doc.revision !== 'string' || !ID.test(doc.revision) || !iso(doc.updated) || typeof doc.proof !== 'string'
        || doc.proof.length > 512 || !Array.isArray(doc.pins) || doc.pins.length > MAX_ROOM_PINS) return null;
    const pins = [], seen = new Set();
    for (const pin of doc.pins) {
        if (!pinTarget(container, pin?.target) || !iso(pin.created) || typeof pin.id !== 'string' || !ID.test(pin.id) || seen.has(pin.target)) return null;
        seen.add(pin.target);
        pins.push({ target: pin.target, created: pin.created, id: pin.id });
    }
    pins.sort((a, b) => compare(a.target, b.target));
    return { v: 1, index: doc.index, owner, revision: doc.revision, updated: doc.updated, pins, proof: doc.proof };
}

export function createPodPins({ fetch, allowed, getWebId, getSessionId, getSigner, fetchPeerSigner, peerPodRoot, readDay,
    retryDelay = () => new Promise(resolve => setTimeout(resolve, 250)) }) {
    const writes = new Map();
    const fail = (reason = 'unavailable', status = 0) => ({ ok: false, reason, status });
    function scope(container, current) {
        const session = getSessionId();
        return () => current() && session === getSessionId() && allowed(container);
    }
    // Pin discovery and Jump are read-only. Recover once from a transient pod
    // or gateway failure, but never retry permission/signature errors or PUTs.
    async function retryRead(read, current) {
        for (let attempt = 0; attempt < 2; attempt++) {
            if (!current()) return fail('account_changed');
            let result;
            try { result = await read(); } catch { result = fail(); }
            if (!current()) return fail('account_changed');
            if (attempt || result?.ok || (result?.reason && result.reason !== 'unavailable')
                || ![0, 502, 503, 504].includes(result?.status || 0)) return result;
            await retryDelay();
        }
    }
    const index = (container, owner, current) => retryRead(() => indexOnce(container, owner, current), current);
    async function indexOnce(container, owner, current) {
        if (!current() || !peerPodRoot(owner)) return fail();
        try {
            const response = await fetch(container + 'pins.json', { headers: { Accept: 'application/json' }, cache: 'no-store', redirect: 'error' });
            if (!current()) return fail('account_changed');
            if (response.status === 404) return { ok: true, exists: false, pins: [], snapshot: null, etag: null };
            if (!response.ok) return fail('unavailable', response.status);
            if (Number(response.headers?.get?.('content-length')) > MAX_BYTES) return fail('invalid_index');
            const text = await response.text();
            if (!current()) return fail('account_changed');
            if (new TextEncoder().encode(text).length > MAX_BYTES) return fail('invalid_index');
            const snapshot = normalizePinSnapshot(JSON.parse(text), container, owner);
            if (!snapshot) return fail('invalid_index');
            const signer = await verifyRoomPins(snapshot);
            if (!current()) return fail('account_changed');
            if (!signer) return fail('invalid_index');
            const published = await fetchPeerSigner(peerPodRoot(owner));
            if (!current()) return fail('account_changed');
            if (published?.signer !== signer) return fail('invalid_index');
            return { ok: true, exists: true, snapshot, pins: snapshot.pins, etag: response.headers?.get?.('etag') };
        } catch { return fail(); }
    }

    async function readMessage(container, target, owner, isCurrent = () => true) {
        const current = scope(container, isCurrent), ref = pinTarget(container, target);
        if (!ref || !current()) return fail();
        const day = await retryRead(() => readDay(container, ref.date, '', owner), current);
        if (!current()) return fail('account_changed');
        if (!day.ok) return fail('unavailable', day.status);
        const message = day.messages.find(m => m.iri === target && !m.deleted);
        return { ok: true, message: message || null };
    }

    async function readPins(container, owner, isCurrent = () => true) {
        const current = scope(container, isCurrent), version = await index(container, owner, current);
        if (!version.ok) return version;
        const days = new Map(), pins = [];
        for (const pin of [...version.pins].sort((a, b) => compare(b.created, a.created) || compare(b.id, a.id))) {
            if (!current()) return fail('account_changed');
            const ref = pinTarget(container, pin.target);
            if (!days.has(ref.date)) days.set(ref.date, await retryRead(() => readDay(container, ref.date, '', owner), current));
            if (!current()) return fail('account_changed');
            const day = days.get(ref.date);
            if (!day.ok) return fail('unavailable', day.status);
            const message = day.messages.find(m => m.iri === pin.target && !m.deleted);
            if (message) pins.push({ message_id: message.message_id, content: message.content,
                pinned_by: owner, pinned_at: pin.created, pin_id: pin.id, target: pin.target, message });
        }
        return { ok: true, pins };
    }

    function setPin(container, messageId, timestamp, add, owner, expectedPinId = null, isCurrent = () => true) {
        const current = scope(container, isCurrent), actor = getWebId();
        if (!current() || actor !== owner) return Promise.resolve(fail('forbidden', 403));
        if (!ID.test(messageId || '') || !Number.isFinite(Date.parse(timestamp))) return Promise.resolve(fail());
        const target = messageIriAt(container, messageId, timestamp);
        if (!pinTarget(container, target)) return Promise.resolve(fail());
        const key = JSON.stringify([getSessionId(), container]);
        const pending = (writes.get(key) || Promise.resolve()).catch(() => {}).then(async () => {
            for (let attempt = 0; attempt < 4; attempt++) {
                if (!current() || getWebId() !== owner) return fail('account_changed');
                const signer = getSigner();
                if (!signer?.privKey || !signer.signerDid) return fail();
                const version = await index(container, owner, current);
                if (!version.ok) return version;
                let basePins = version.pins;
                const previous = basePins.find(pin => pin.target === target);
                if (!add && !previous) return { ok: true };
                if (!add && expectedPinId && previous.id !== expectedPinId) return fail('conflict');
                if (add) {
                    const message = await readMessage(container, target, owner, current);
                    if (!message.ok) return message;
                    if (!message.message) return fail('deleted');
                    if (previous) return { ok: true };
                    if (basePins.length >= MAX_ROOM_PINS) {
                        // Withdrawn messages are omitted from panels. Reclaim their
                        // slots only after successful authenticated day reads.
                        const live = await readPins(container, owner, current);
                        if (!live.ok) return live;
                        const targets = new Set(live.pins.map(pin => pin.target));
                        basePins = basePins.filter(pin => targets.has(pin.target));
                        if (basePins.length >= MAX_ROOM_PINS) return fail('pin_limit');
                    }
                }
                if (version.exists && !isStrongEtag(version.etag)) return fail('version_unavailable');
                const updated = new Date(Math.max(Date.now(), (Date.parse(version.snapshot?.updated) || 0) + 1)).toISOString();
                const pins = basePins.filter(pin => pin.target !== target);
                const revision = crypto.randomUUID();
                if (add) pins.push({ target, created: updated, id: revision });
                pins.sort((a, b) => compare(a.target, b.target));
                const snapshot = { v: 1, index: container + 'pins.json', owner, revision, updated, pins };
                snapshot.proof = await signRoomPins(snapshot, signer.privKey, signer.signerDid);
                if (!current() || getSigner()?.signerDid !== signer.signerDid) return fail('account_changed');
                if (!snapshot.proof) return fail();
                const body = JSON.stringify(snapshot);
                if (new TextEncoder().encode(body).length > MAX_BYTES) return fail('pin_limit');
                let response;
                try {
                    response = await fetch(snapshot.index, { method: 'PUT', redirect: 'error', body,
                        headers: { 'Content-Type': 'application/json', ...(version.exists ? { 'If-Match': version.etag } : { 'If-None-Match': '*' }) } });
                } catch { return fail(); }
                if (!current()) return fail('account_changed');
                if (response.status === 412) continue;
                if (!response.ok) return fail('unavailable', response.status);
                const saved = await index(container, owner, current);
                if (!saved.ok) return fail('confirmation_failed', saved.status);
                const confirmed = saved.pins.find(pin => pin.target === target);
                if (add ? confirmed?.id !== revision : !!confirmed) return fail('conflict');
                return { ok: true };
            }
            return fail('conflict');
        });
        writes.set(key, pending);
        pending.finally(() => { if (writes.get(key) === pending) writes.delete(key); }).catch(() => {});
        return pending;
    }
    return { readPins, setPin, readMessage };
}
