import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ sender: null, receiver: null }));
vi.mock('./native-edit-send.js', () => ({ createNativeEditSender: deps => {
    state.sender = { deps, enqueue: vi.fn(), retry: vi.fn(async () => true), status: vi.fn(async () => true), recover: vi.fn(async () => true),
        inspect: vi.fn(async () => []), stop: vi.fn() }; return state.sender;
} }));
vi.mock('./native-edit-receive.js', () => ({ createNativeEditReceiver: deps => {
    state.receiver = { deps, receive: vi.fn(async () => true), confirm: vi.fn(async () => true), recover: vi.fn(async () => true), stop: vi.fn() }; return state.receiver;
} }));
import { createNativeEdits } from './native-edits.js';
function harness() {
    let scope = 'account', generation = 0;
    const send = vi.fn(() => true), errors = vi.fn();
    const edits = createNativeEdits({ getScope: () => scope, getGeneration: () => generation, isActive: () => true,
        send, onError: errors });
    return { edits, send, errors, switchAccount: () => { scope = 'other'; generation++; } };
}
beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });
describe('native edit protocol integration', () => {
    it('negotiates only strict version one and leaves ordinary file status to the file controller', async () => {
        const h = harness(); h.edits.start(true);
        expect((await h.edits.save({}, {}, 'caption')).ok).toBe(false);
        expect(state.sender.enqueue).not.toHaveBeenCalled();
        h.edits.start(1); await Promise.resolve();
        expect(await h.edits.handleEvent({ type: 'native_media_status', v: 1 })).toBe(false);
        expect(state.sender.status).not.toHaveBeenCalled();
        expect(await h.edits.handleEvent({ type: 'native_media_status', v: 1, native_edit_v: 1 })).toBe(true);
        expect(state.sender.status).toHaveBeenCalledOnce();
        expect(await h.edits.handleEvent({ type: 'native_edit', native_edit_v: 1 })).toBe(true);
        expect(state.receiver.receive).toHaveBeenCalledOnce(); h.edits.stop();
    });
    it('binds capability to the original file, peer and key, and cancels a pending query on stop', async () => {
        const h = harness(); h.edits.start(1);
        const request = { cert_id: 'thread', file_id: 'file', peer_webid: 'did:key:peer', expected_pub: 'A'.repeat(43) };
        const query = state.sender.deps.capability(request, () => true);
        const packet = h.send.mock.calls.at(-1)[0];
        const result = { type: 'native_edit_capability_result', v: 1, native_edit_v: 1, request_id: packet.request_id,
            file_id: 'file', peer_webid: request.peer_webid, expected_pub: request.expected_pub,
            supported: true, peer_pub: request.expected_pub, native_sequence: 1, parent_header_digest: 'a'.repeat(64) };
        let settled = false; query.then(() => { settled = true; });
        for (const patch of [{ file_id: 'foreign' }, { peer_webid: 'foreign' }, { expected_pub: 'B'.repeat(43) }, { native_edit_v: true }]) {
            await h.edits.handleEvent({ ...result, ...patch }); expect(settled).toBe(false);
        }
        await h.edits.handleEvent(result); expect(await query).toEqual(result);
        const next = state.sender.deps.capability(request, () => true);
        h.edits.stop(); expect(await next).toBeNull();
    });
    it('bounds pending capability requests and resolves timeouts without preparing a send', async () => {
        const h = harness(); h.edits.start(1);
        const request = { cert_id: 'thread', file_id: 'file', peer_webid: 'did:key:peer', expected_pub: 'A'.repeat(43) };
        const queries = Array.from({ length: 10 }, () => state.sender.deps.capability(request, () => true));
        expect(await state.sender.deps.capability(request, () => true)).toBeNull();
        expect(h.send).toHaveBeenCalledTimes(10);
        await vi.advanceTimersByTimeAsync(6001);
        expect(await Promise.all(queries)).toEqual(Array(10).fill(null)); h.edits.stop();
    });
    it('coalesces recovery and suppresses the old sender after an account switch', async () => {
        const h = harness(); let release;
        state.receiver.recover.mockImplementation(() => new Promise(resolve => { release = resolve; }));
        h.edits.start(1); const first = h.edits.recover(), second = h.edits.recover();
        expect(state.receiver.recover).toHaveBeenCalledOnce();
        h.switchAccount(); release(); expect(await first).toBe(false); expect(await second).toBe(false);
        expect(state.sender.recover).not.toHaveBeenCalled(); h.edits.stop();
    });
    it('exposes retained caption jobs with retry and no cancellation while disconnected', async () => {
        const h = harness(); state.sender.inspect.mockResolvedValue([{ id: 'ne-action', peer: 'did:key:peer', state: 'header-pending', canCancel: false }]);
        const rows = await h.edits.inspect(); expect(rows).toEqual([expect.objectContaining({ id: 'native-edit:ne-action', state: 'nativeConnection', canRetry: false, canCancel: false })]);
        h.edits.start(1); expect((await h.edits.inspect())[0].state).toBe('nativeHeader'); h.edits.stop();
    });
});
