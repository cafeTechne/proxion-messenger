/**
 * pod.js integration tests — run against a live CSS Docker instance.
 *
 * Before running: start css-alice (`docker compose -f docker-compose.test.yml up -d css-alice`)
 * and provision credentials (`python scripts/provision_test_pod.py`).
 *
 * If TEST_CSS_CLIENT_ID is not set, all tests are skipped.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';

// Live bindings for the auth.js mock — updated in beforeAll.
let _session = null;
let _storageRoot = null;

// vi.mock is hoisted before all imports; factory closure captures _session/_storageRoot by reference.
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import {
    podWriteMessage,
    podWriteMessageWithIndex,
    podWriteRoomMeta,
    podReadMessages,
    podReadRoomMeta,
    podDeleteMessage,
    podWriteMessageJsonLd,
    podWriteRoomMembers,
    podWriteReactions,
    podWriteReadState,
    podReadReadState,
    podWriteScheduled,
    podDeleteScheduled,
    podWriteWebhook,
    podDeleteWebhook,
    ensureProxionContainer,
} from './pod.js';

// ── helpers ──────────────────────────────────────────────────────────────────

function uid(prefix = 'test') {
    return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

function skipIfNoCss() {
    if (!process.env.TEST_CSS_CLIENT_ID) {
        return true;
    }
    return false;
}

function makeMsg(roomId, extra = {}) {
    return {
        message_id: uid('msg'),
        content: 'hello pod',
        timestamp: new Date().toISOString(),
        from_webid: process.env.TEST_WEBID || 'https://example.com/profile#me',
        ...extra,
    };
}

// ── auth setup ───────────────────────────────────────────────────────────────

beforeAll(async () => {
    if (!process.env.TEST_CSS_CLIENT_ID) return;

    const { Session } = await import('@inrupt/solid-client-authn-node');
    _session = new Session();
    await _session.login({
        clientId: process.env.TEST_CSS_CLIENT_ID,
        clientSecret: process.env.TEST_CSS_CLIENT_SECRET,
        oidcIssuer: process.env.TEST_CSS_ISSUER,
    });

    _storageRoot = process.env.TEST_STORAGE_ROOT;
});

afterAll(async () => {
    if (_session) await _session.logout();
});

// ── ensureProxionContainer ────────────────────────────────────────────────────

describe('ensureProxionContainer', () => {
    it('creates proxion/ container without error', async () => {
        if (skipIfNoCss()) return;
        await expect(ensureProxionContainer()).resolves.not.toThrow();
    });
});

// ── podWriteRoomMeta / podReadRoomMeta ────────────────────────────────────────

describe('podWriteRoomMeta + podReadRoomMeta', () => {
    it('round-trips room metadata', async () => {
        if (skipIfNoCss()) return;
        const roomId = uid('room');
        const meta = { room_id: roomId, name: 'Test Room', created_at: new Date().toISOString() };
        await podWriteRoomMeta(roomId, meta);
        const fetched = await podReadRoomMeta(roomId);
        expect(fetched).not.toBeNull();
        expect(fetched.room_id).toBe(roomId);
        expect(fetched.name).toBe('Test Room');
    });

    it('returns null for unknown room', async () => {
        if (skipIfNoCss()) return;
        const result = await podReadRoomMeta('nonexistent-room-zzzz');
        expect(result).toBeNull();
    });
});

// ── podWriteMessage / podReadMessages ─────────────────────────────────────────

describe('podWriteMessageWithIndex + podReadMessages', () => {
    it('writes a message and reads it back', async () => {
        if (skipIfNoCss()) return;
        const roomId = uid('room');
        const msg = makeMsg(roomId);
        await podWriteMessageWithIndex(roomId, msg);
        const msgs = await podReadMessages(roomId);
        expect(msgs.length).toBeGreaterThanOrEqual(1);
        const found = msgs.find(m => m.message_id === msg.message_id);
        expect(found).toBeDefined();
        expect(found.content).toBe('hello pod');
    });

    it('returns empty array for unknown room', async () => {
        if (skipIfNoCss()) return;
        const result = await podReadMessages('nonexistent-room-zzzz');
        expect(result).toEqual([]);
    });

    it('accumulates multiple messages in index order', async () => {
        if (skipIfNoCss()) return;
        const roomId = uid('room');
        const msgs = [
            makeMsg(roomId, { content: 'first', timestamp: '2026-01-01T00:00:00Z' }),
            makeMsg(roomId, { content: 'second', timestamp: '2026-01-01T00:00:01Z' }),
            makeMsg(roomId, { content: 'third', timestamp: '2026-01-01T00:00:02Z' }),
        ];
        for (const m of msgs) await podWriteMessageWithIndex(roomId, m);
        const fetched = await podReadMessages(roomId);
        const contents = fetched.map(m => m.content);
        expect(contents).toContain('first');
        expect(contents).toContain('second');
        expect(contents).toContain('third');
    });
});

// ── podDeleteMessage (JSON-LD path) ───────────────────────────────────────────

describe('podWriteMessageJsonLd + podDeleteMessage', () => {
    it('writes then deletes a message without error', async () => {
        if (skipIfNoCss()) return;
        const threadId = uid('room');
        const messageId = uid('msg');
        const msg = { content: 'temp', from_webid: process.env.TEST_WEBID || '', timestamp: new Date().toISOString() };
        await podWriteMessageJsonLd(threadId, messageId, msg, true);
        await expect(podDeleteMessage(threadId, messageId, true)).resolves.not.toThrow();
    });
});

// ── podWriteReactions ─────────────────────────────────────────────────────────

describe('podWriteReactions', () => {
    it('writes reactions without error', async () => {
        if (skipIfNoCss()) return;
        const roomId = uid('room');
        const messageId = uid('msg');
        const reactions = { '👍': ['https://example.com/profile#me'] };
        await expect(podWriteReactions(roomId, messageId, reactions)).resolves.not.toThrow();
    });
});

// ── podWriteReadState / podReadReadState ──────────────────────────────────────

describe('podWriteReadState + podReadReadState', () => {
    it('round-trips read state', async () => {
        if (skipIfNoCss()) return;
        const threadId = uid('thread');
        const lastId = uid('msg');
        await podWriteReadState(threadId, lastId);
        const state = await podReadReadState(threadId);
        expect(state).not.toBeNull();
        expect(state['px:threadId']).toBe(threadId);
        expect(state['px:lastReadMessageId']).toBe(lastId);
    });

    it('returns null for unknown thread', async () => {
        if (skipIfNoCss()) return;
        const result = await podReadReadState('nonexistent-thread-zzzz');
        expect(result).toBeNull();
    });
});

// ── podWriteScheduled / podDeleteScheduled ────────────────────────────────────

describe('podWriteScheduled + podDeleteScheduled', () => {
    it('writes and then deletes a scheduled message', async () => {
        if (skipIfNoCss()) return;
        const id = uid('sched');
        const threadId = uid('thread');
        await podWriteScheduled(id, threadId, new Date().toISOString(), 'preview text');
        await expect(podDeleteScheduled(id)).resolves.not.toThrow();
    });
});

// ── podWriteWebhook / podDeleteWebhook ────────────────────────────────────────

describe('podWriteWebhook + podDeleteWebhook', () => {
    it('writes and then deletes a webhook', async () => {
        if (skipIfNoCss()) return;
        const id = uid('wh');
        const wh = { direction: 'incoming', bot_name: 'TestBot', token: 'tok-abc' };
        await podWriteWebhook(id, wh);
        await expect(podDeleteWebhook(id)).resolves.not.toThrow();
    });
});

// ── input validation (no CSS needed) ─────────────────────────────────────────

describe('input validation (no CSS needed)', () => {
    it('podWriteMessage silently returns on invalid roomId', async () => {
        const msg = makeMsg('ok', { message_id: 'ok-id' });
        // Even with live session, invalid ID must be a no-op
        await expect(podWriteMessage('../etc/passwd', msg)).resolves.not.toThrow();
    });

    it('podReadMessages returns [] on invalid roomId', async () => {
        const result = await podReadMessages('../evil');
        expect(result).toEqual([]);
    });

    it('podReadRoomMeta returns null on invalid roomId', async () => {
        const result = await podReadRoomMeta('../evil');
        expect(result).toBeNull();
    });
});
