// blocks.test.js — R65: client block feature (state, actions, hydrate).
import { describe, it, expect, vi, beforeEach } from 'vitest';

// pod.js is imported by blocks.js; stub podWriteBlocks so no pod call happens.
vi.mock('./pod.js', () => ({ podWriteBlocks: vi.fn(async () => {}) }));

import { createBlocks } from './blocks.js';

let sent;
function make(over = {}) {
    sent = [];
    const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
    const b = createBlocks({
        getSocket: () => (over.socket === undefined ? socket : over.socket),
        showToast: () => {},
        onAfterChange: over.onAfterChange || (() => {}),
    });
    return b;
}

beforeEach(() => {
    global.WebSocket = { OPEN: 1 };
    global.document = { getElementById: () => null };
});

describe('block / unblock', () => {
    it('block adds to the set and sends the command', () => {
        const b = make();
        expect(b.isBlocked('did:key:zEvil')).toBe(false);
        b.block('did:key:zEvil');
        expect(b.isBlocked('did:key:zEvil')).toBe(true);
        expect(sent).toContainEqual({ cmd: 'block', webid: 'did:key:zEvil' });
    });

    it('unblock removes and sends the command', () => {
        const b = make();
        b.block('did:key:zEvil');
        sent = [];
        b.unblock('did:key:zEvil');
        expect(b.isBlocked('did:key:zEvil')).toBe(false);
        expect(sent).toContainEqual({ cmd: 'unblock', webid: 'did:key:zEvil' });
    });

    it('toggle flips state', () => {
        const b = make();
        b.toggle('x');
        expect(b.isBlocked('x')).toBe(true);
        b.toggle('x');
        expect(b.isBlocked('x')).toBe(false);
    });

    it('block is idempotent (no duplicate command)', () => {
        const b = make();
        b.block('x');
        sent = [];
        b.block('x');
        expect(sent).toHaveLength(0);
    });

    it('fires onAfterChange on changes', () => {
        const cb = vi.fn();
        const b = make({ onAfterChange: cb });
        b.block('x');
        expect(cb).toHaveBeenCalled();
    });
});

describe('handleBlocksEvent (authoritative from gateway)', () => {
    it('replaces the set with the gateway list', () => {
        const b = make();
        b.block('local-only');
        b.handleBlocksEvent(['did:key:zA', 'did:key:zB']);
        expect(b.isBlocked('local-only')).toBe(false);
        expect(b.isBlocked('did:key:zA')).toBe(true);
        expect(b.isBlocked('did:key:zB')).toBe(true);
    });
    it('ignores non-strings', () => {
        const b = make();
        b.handleBlocksEvent(['ok', 42, null]);
        expect(b.isBlocked('ok')).toBe(true);
    });
});

describe('requestBlocks', () => {
    it('sends list_blocks', () => {
        const b = make();
        b.requestBlocks();
        expect(sent).toContainEqual({ cmd: 'list_blocks' });
    });
});

describe('reconcileFromPod', () => {
    it('blocks pod entries not already blocked, keeps local-only blocks', () => {
        const b = make();
        b.block('local-only');
        sent = [];
        b.reconcileFromPod(['did:key:zPod', 'local-only']);
        expect(b.isBlocked('did:key:zPod')).toBe(true);   // added from pod
        expect(b.isBlocked('local-only')).toBe(true);      // not dropped
        expect(sent).toContainEqual({ cmd: 'block', webid: 'did:key:zPod' });
        expect(sent).not.toContainEqual({ cmd: 'block', webid: 'local-only' });
    });
    it('ignores non-arrays', () => {
        const b = make();
        expect(() => b.reconcileFromPod(null)).not.toThrow();
    });
});
