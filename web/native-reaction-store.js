import { validNativeReaction } from './native-reaction-codec.js';
import { nativeMessageIdForRef } from './native-message-ref.js';

// Tombstones are deliberately retained. Evicting an old remove would allow a
// delayed add to resurrect it. A full store fails closed before receive ACK.
const LIMIT = 2000;
const DB = 'proxion-native-reactions-v1';
export function createNativeReactionStore({ getScope, enabled = () => true, databaseName = DB } = {}) {
    async function transaction(mode, current, operation) {
        const scope = getScope();
        const valid = () => typeof scope === 'string' && !!scope && enabled() && current() && scope === getScope();
        if (!valid()) return false;
        let db;
        try {
            db = await new Promise((resolve, reject) => {
                let blocked = false;
                const request = indexedDB.open(databaseName, 1);
                request.onupgradeneeded = () => {
                    const store = request.result.createObjectStore('actions', { keyPath: 'key' });
                    store.createIndex('scope', 'scope');
                };
                request.onsuccess = () => { if (blocked) request.result.close(); else resolve(request.result); };
                request.onerror = () => reject(request.error);
                request.onblocked = () => { blocked = true; reject(Error('Reaction storage blocked')); };
            });
            if (!valid()) return false;
            return await new Promise(resolve => {
                const tx = db.transaction('actions', mode), store = tx.objectStore('actions');
                let result = false;
                const abort = () => { try { tx.abort(); } catch { /* already aborted */ } };
                const finish = value => { if (!valid()) abort(); else result = value; };
                tx.oncomplete = () => resolve(valid() ? result : false);
                tx.onabort = tx.onerror = () => resolve(false);
                try { operation({ store, scope, valid, finish, abort }); } catch { abort(); }
            });
        } catch { return false; }
        finally { db?.close(); }
    }
    function target(body, context) {
        return validNativeReaction(body) ? nativeMessageIdForRef(body.ref, context) : null;
    }
    return {
        async apply(body, context, current = () => true) {
            const id = target(body, context);
            if (!id) return false;
            // Copy before awaiting IDB; a caller cannot mutate a queued action.
            const action = { mid: body.mid, tok: body.tok, seq: body.seq, op: body.op, emoji: body.emoji };
            const { threadId, actorWebId } = context;
            return transaction('readwrite', current, ({ store, scope, valid, finish, abort }) => {
                const key = [scope, threadId, id, actorWebId, action.emoji];
                const request = store.get(key);
                request.onsuccess = () => {
                    if (!valid()) return abort();
                    const previous = request.result;
                    if (previous) {
                        if (previous.tok !== action.tok) return abort();
                        if (previous.seq > action.seq) return finish(true);
                        if (previous.seq === action.seq) {
                            if (previous.op !== action.op || previous.mid !== action.mid) return abort();
                            return finish(true);
                        }
                        store.put({ key, scope, threadId, target: id, actorWebId, ...action }); finish(true);
                    } else {
                        const count = store.index('scope').count(scope);
                        count.onsuccess = () => {
                            if (!valid() || count.result >= LIMIT) return abort();
                            store.put({ key, scope, threadId, target: id, actorWebId, ...action }); finish(true);
                        };
                    }
                };
            });
        },
        async rows(context, current = () => true) {
            // Validate the authenticated participant set even if there is no
            // retained attachment. Metadata alone must never create a bubble.
            if (!nativeMessageIdForRef({ v: 1, kind: 'native_file', id: 'probe', author: 'actor' }, context)) return false;
            const { threadId, selfWebId, peerWebId } = context;
            return transaction('readonly', current, ({ store, scope, finish }) => {
                const request = store.index('scope').getAll(scope);
                request.onsuccess = () => finish(request.result.filter(row => row.threadId === threadId
                    && [selfWebId, peerWebId].includes(row.actorWebId)).map(({ key: _key, scope: _scope, ...row }) => row));
            });
        },
    };
}
