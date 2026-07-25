// solidchat.test.js — the cross-app conversation model. Pure mock: auth.js and
// the pod primitives are stubbed so we exercise the model, not the network.
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _webId = 'https://me.pod/profile/card#me';
let _root = 'https://me.pod/';

vi.mock('./auth.js', () => ({
    get solidSession() { return { info: { isLoggedIn: !!_webId, webId: _webId } }; },
    podStorageRoot: () => _root,
}));

// Record calls to the pod primitives; control their return values per test.
const pod = {
    writes: [], reads: [], grants: [],
    writeOk: true, grantOk: true, readResult: [],
};
vi.mock('./pod.js', () => ({
    podWriteChatMessageAt: vi.fn(async (container, id, msg) => { pod.writes.push({ container, id, msg }); return pod.writeOk; }),
    podReadChatRecentAt: vi.fn(async (container, days, thread) => { pod.reads.push({ container, days, thread }); return pod.readResult; }),
    podGrantChatParticipants: vi.fn(async (container, owner, parts) => { pod.grants.push({ container, owner, parts }); return pod.grantOk; }),
}));

import { createSolidChat, isValidChatContainer } from './solidchat.js';

let store;
beforeEach(() => {
    store = {};
    global.localStorage = {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: (k) => { delete store[k]; },
    };
    _webId = 'https://me.pod/profile/card#me';
    _root = 'https://me.pod/';
    pod.writes = []; pod.reads = []; pod.grants = [];
    pod.writeOk = true; pod.grantOk = true; pod.readResult = [];
});

function make(over = {}) {
    const toasts = [];
    const sc = createSolidChat({ showToast: (m) => toasts.push(m), onChange: over.onChange || (() => {}) });
    return { sc, toasts };
}

describe('isValidChatContainer', () => {
    it('accepts an absolute container URL and rejects junk', () => {
        expect(isValidChatContainer('https://alice.pod/OurChat/')).toBe(true);
        expect(isValidChatContainer('http://localhost:3001/x/rooms/y/')).toBe(true);
        expect(isValidChatContainer('https://alice.pod/OurChat')).toBe(false);   // no trailing /
        expect(isValidChatContainer('ftp://x/')).toBe(false);
        expect(isValidChatContainer('https://a.pod/../etc/')).toBe(false);       // traversal
        expect(isValidChatContainer('https://a.pod/x/?q=1/')).toBe(false);       // query
        expect(isValidChatContainer('not a url')).toBe(false);
        expect(isValidChatContainer(null)).toBe(false);
    });
});

describe('hostConversation', () => {
    it('creates a chat in our pod, grants participants, and records it', async () => {
        const { sc } = make();
        const bob = 'https://bob.pod/profile/card#me';
        const conv = await sc.hostConversation({ title: 'Design', participantWebIds: [bob] });
        expect(conv).toBeTruthy();
        expect(conv.role).toBe('host');
        expect(conv.id.startsWith('https://me.pod/proxion/rooms/sc-')).toBe(true);
        expect(conv.id.endsWith('/')).toBe(true);
        // participant grant happened, owner excluded, bob included
        expect(pod.grants).toHaveLength(1);
        expect(pod.grants[0].parts).toEqual([bob]);
        // it is in the list
        expect(sc.listConversations().map(c => c.id)).toContain(conv.id);
    });

    it('excludes ourselves from the participant grant and dedupes', async () => {
        const { sc } = make();
        const bob = 'https://bob.pod/profile/card#me';
        await sc.hostConversation({ title: 'x', participantWebIds: [_webId, bob, bob] });
        expect(pod.grants[0].parts).toEqual([bob]);
    });

    it('refuses without a pod', async () => {
        _root = null;
        const { sc, toasts } = make();
        expect(await sc.hostConversation({ title: 'x' })).toBeNull();
        expect(toasts.join(' ')).toMatch(/pod/i);
    });

    it('does not record the conversation if the grant fails', async () => {
        pod.grantOk = false;
        const { sc } = make();
        expect(await sc.hostConversation({ title: 'x' })).toBeNull();
        expect(sc.listConversations()).toHaveLength(0);
    });
});

describe('joinConversation', () => {
    const URL = 'https://alice.pod/OurChat/';
    it('records a conversation after confirming read access', async () => {
        const { sc } = make();
        const conv = await sc.joinConversation(URL, { title: 'Alice chat' });
        expect(conv).toBeTruthy();
        expect(conv.role).toBe('participant');
        expect(conv.id).toBe(URL);
        expect(pod.reads[0].container).toBe(URL);   // access check happened
        expect(sc.getConversation(URL)).toBeTruthy();
    });

    it('rejects an invalid link before touching the network', async () => {
        const { sc, toasts } = make();
        expect(await sc.joinConversation('nope')).toBeNull();
        expect(pod.reads).toHaveLength(0);
        expect(toasts.join(' ')).toMatch(/valid/i);
    });

    it('fails loudly when the chat cannot be read', async () => {
        const { createSolidChat: fresh } = await import('./solidchat.js');
        // Make the read throw for this test.
        const podmod = await import('./pod.js');
        podmod.podReadChatRecentAt.mockImplementationOnce(async () => { throw new Error('403'); });
        const toasts = [];
        const sc = fresh({ showToast: (m) => toasts.push(m) });
        expect(await sc.joinConversation(URL)).toBeNull();
        expect(toasts.join(' ')).toMatch(/access/i);
        expect(sc.getConversation(URL)).toBeNull();
    });
});

describe('sendMessage', () => {
    it('posts as ourselves and stamps the conversation', async () => {
        const { sc } = make();
        const conv = await sc.hostConversation({ title: 'x' });
        const ok = await sc.sendMessage(conv.id, '  hello  ');
        expect(ok).toBe(true);
        expect(pod.writes).toHaveLength(1);
        expect(pod.writes[0].container).toBe(conv.id);
        expect(pod.writes[0].msg.content).toBe('hello');          // trimmed
        expect(pod.writes[0].msg.from_webid).toBe(_webId);
        expect(sc.getConversation(conv.id).lastAt).toBeTruthy();  // stamped
    });

    it('is a no-op for blank text or an unknown conversation', async () => {
        const { sc } = make();
        const conv = await sc.hostConversation({ title: 'x' });
        expect(await sc.sendMessage(conv.id, '   ')).toBe(false);
        expect(await sc.sendMessage('https://nope.pod/x/', 'hi')).toBe(false);
        expect(pod.writes).toHaveLength(0);
    });

    it('reports failure and does not stamp when the write fails', async () => {
        pod.writeOk = false;
        const { sc, toasts } = make();
        const conv = await sc.hostConversation({ title: 'x' });
        expect(await sc.sendMessage(conv.id, 'hi')).toBe(false);
        expect(toasts.join(' ')).toMatch(/not sent/i);
        expect(sc.getConversation(conv.id).lastAt).toBeNull();
    });
});

describe('list / leave / persistence', () => {
    it('persists across instances via localStorage', async () => {
        const { sc } = make();
        await sc.hostConversation({ title: 'A' });
        const { sc: sc2 } = make();       // new instance, same localStorage
        expect(sc2.listConversations()).toHaveLength(1);
    });

    it('leaveConversation removes it from our list only', async () => {
        const { sc } = make();
        const conv = await sc.hostConversation({ title: 'A' });
        sc.leaveConversation(conv.id);
        expect(sc.listConversations()).toHaveLength(0);
        expect(pod.writes).toHaveLength(0);   // no pod deletion
    });

    it('notifies onChange when the list mutates', async () => {
        const changes = [];
        const { sc } = make({ onChange: (l) => changes.push(l.length) });
        await sc.hostConversation({ title: 'A' });
        expect(changes[changes.length - 1]).toBe(1);
    });

    it('sorts most-recently-active first', async () => {
        const { sc } = make();
        const a = await sc.hostConversation({ title: 'A' });
        const b = await sc.hostConversation({ title: 'B' });
        await sc.sendMessage(a.id, 'ping');   // A is now more recent
        expect(sc.listConversations()[0].id).toBe(a.id);
    });
});

describe('subscribeConversation (live update by polling)', () => {
    it('emits only NEW messages on each poll, and stops on unsubscribe', async () => {
        vi.useFakeTimers();
        try {
            const { sc } = make();
            const conv = await sc.hostConversation({ title: 'A' });
            const emitted = [];
            // The conversation returns a growing message list across polls.
            let msgs = [{ message_id: 'm1', content: 'one' }];
            pod.readResult = msgs;
            const unsub = sc.subscribeConversation(conv.id, (fresh) => emitted.push(...fresh.map(m => m.message_id)), { intervalMs: 1000 });
            await vi.advanceTimersByTimeAsync(0);          // first tick
            expect(emitted).toEqual(['m1']);

            pod.readResult = [...msgs, { message_id: 'm2', content: 'two' }];
            await vi.advanceTimersByTimeAsync(1000);       // second tick
            expect(emitted).toEqual(['m1', 'm2']);         // only the new one added

            await vi.advanceTimersByTimeAsync(1000);       // no change -> nothing new
            expect(emitted).toEqual(['m1', 'm2']);

            unsub();
            pod.readResult = [...pod.readResult, { message_id: 'm3' }];
            await vi.advanceTimersByTimeAsync(5000);       // stopped: no more emissions
            expect(emitted).toEqual(['m1', 'm2']);
        } finally {
            vi.useRealTimers();
        }
    });
});
