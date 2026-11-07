import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
function declaration(name) {
    const start = new RegExp(`(?:async )?function ${name}\\(`).exec(source).index;
    const tail = source.slice(start), end = /^        }\r?$/m.exec(tail);
    return tail.slice(0, end.index + end[0].length);
}
function compile(name, deps) {
    return new Function(...Object.keys(deps), `${declaration(name)}; return ${name};`)(...Object.values(deps));
}
function harness(active = true) {
    const message = { message_id: 'm1', thread_id: 'room', content: 'Original', own: true };
    const deps = {
        activeView: active ? { id: 'room', type: 'local_room' } : { id: 'other', type: 'local_room' },
        messageMap: { m1: { ...message } }, allMessages: [{ ...message }],
        _webRoomCache: { room: [{ ...message }] },
        roomLastMessages: { room: { message_id: 'm1', snippet: 'Original' } },
        pinActions: { messageChanged: vi.fn() }, edit: { state: { editingMsgId: null } }, cancelEdit: vi.fn(),
        _handleEventAsync: vi.fn(), renderReactions: vi.fn(), updateRoomPreview: vi.fn(), markdownToPlain: text => text,
        forgetSavedMessage: vi.fn(async () => {}), _refreshSavedPanel: vi.fn(),
    };
    return { deps, apply: compile('_onRoomPodChanges', deps) };
}
describe('room change application', () => {
    it('refreshes separate echo, scroll buffer, and history copies with the actual room id', () => {
        const h = harness();
        h.apply('room', [{ message_id: 'm1', content: 'Edited', revision_id: 'rev', thread_id: '' }]);
        for (const m of [h.deps.messageMap.m1, h.deps.allMessages[0], h.deps._webRoomCache.room[0]]) {
            expect(m).toMatchObject({ content: 'Edited', revision_id: 'rev', thread_id: 'room', own: true });
        }
        expect(h.deps._handleEventAsync).toHaveBeenCalledWith(expect.objectContaining({ type: 'message_edited', pod_change: true, revision_id: 'rev' }), true);
    });
    it('updates inactive cached previews and removes withdrawn bookmarks without touching another feed', () => {
        const h = harness(false);
        h.apply('room', [{ message_id: 'm1', content: 'Edited' }]);
        expect(h.deps.roomLastMessages.room.snippet).toBe('Edited');
        h.apply('room', [{ message_id: 'm1', content: '', deleted: true }]);
        expect(h.deps._webRoomCache.room).toEqual([]);
        expect(h.deps.roomLastMessages.room).toBeUndefined();
        expect(h.deps.forgetSavedMessage).toHaveBeenCalledWith('m1');
        expect(h.deps._handleEventAsync).not.toHaveBeenCalled();
    });
    it('refreshes reaction pills without marking text edited or creating message events', () => {
        const h = harness();
        h.apply('room', [{ message_id: 'm1', content: 'Original', reactions: { '👍': ['https://bob/#me'] } }]);
        expect(h.deps.messageMap.m1.reactions).toEqual({ '👍': ['https://bob/#me'] });
        expect(h.deps._webRoomCache.room[0].reactions).toEqual({ '👍': ['https://bob/#me'] });
        expect(h.deps.renderReactions).toHaveBeenCalledWith('m1');
        expect(h.deps._handleEventAsync).not.toHaveBeenCalled();
    });
    it('closes an editor when a verified withdrawal arrives', () => {
        const h = harness(); h.deps.edit.state.editingMsgId = 'm1';
        h.apply('room', [{ message_id: 'm1', content: '', deleted: true }]);
        expect(h.deps.cancelEdit).toHaveBeenCalledWith('m1');
        expect(h.deps._handleEventAsync).toHaveBeenCalledWith(expect.objectContaining({ type: 'message_deleted', pod_change: true }), true);
    });
    it('discards an old account confirmation after the session changes', async () => {
        let session = 1, resolve;
        const io = vi.fn(() => new Promise(r => { resolve = r; }));
        const apply = vi.fn();
        const change = compile('_changePodRoomMessage', {
            _webRoomSessionId: () => session, messageMap: { m1: { timestamp: '2026-11-07T12:00:00Z' } },
            crossRooms: null, _remoteRooms: { room: { container: 'https://pod/room/', owner: 'https://pod/#me' } },
            podChangeMessageAt: io, selfWebId: 'https://pod/#me', _onRoomPodChanges: apply,
        });
        const pending = change({ id: 'room' }, 'm1', { kind: 'edit', content: 'Saved' });
        session++;
        resolve({ ok: true, message: { content: 'Saved' } });
        expect(await pending).toMatchObject({ ok: false, reason: 'account_changed' });
        expect(apply).not.toHaveBeenCalled();
        expect(io.mock.calls[0][5]()).toBe(false);
    });
});

describe('floating members panel dismissal', () => {
    it.each([500, 1000, 1280])('keeps message controls reachable at %ipx', width => {
        const start = source.indexOf('// Dismiss floating members panels');
        const end = source.indexOf('// The pinned and saved drawers', start);
        const handler = source.slice(source.indexOf("document.addEventListener('click'", start), end);
        let click;
        const classes = new Set(['mobile-open']);
        const panel = { style: { display: 'block' }, classList: { remove: c => classes.delete(c) } };
        new Function('document', 'window', handler)({
            addEventListener: (_type, fn) => click = fn,
            getElementById: () => panel,
        }, { innerWidth: width });
        click({ target: { closest: selector => selector === '#members-panel' } });
        expect(panel.style.display).toBe('block');
        click({ target: { closest: () => null } });
        if (width <= 1100) expect(classes.has('mobile-open')).toBe(false);
        if (width === 1000) expect(panel.style.display).toBe('none');
        if (width === 1280) expect(panel.style.display).toBe('block');
    });
});


describe('voted poll edit confirmation', () => {
    it.each([false, true])('only retries the edit with reset consent when approved=%s', async approved => {
        const change = vi.fn().mockResolvedValueOnce({ ok: false, reason: 'poll_has_votes' }).mockResolvedValueOnce({ ok: true });
        let answer;
        const edit = compile('_savePodRoomEdit', {
            _webRoomSessionId: () => 1, messageMap: { poll: { content: 'Original', timestamp: '2026-11-07T12:00:00Z' } },
            activeView: { id: 'room', type: 'local_room' }, _changePodRoomMessage: change,
            showConfirm: (_text, yes, no) => { answer = approved ? yes : no; }, t: key => key,
        });
        const pending = edit({ id: 'room', type: 'local_room' }, 'poll', 'Revised', 'rev1');
        await Promise.resolve(); answer();
        const result = await pending;
        expect(result.ok).toBe(approved);
        expect(change).toHaveBeenCalledTimes(approved ? 2 : 1);
        if (approved) expect(change.mock.calls[1][2]).toMatchObject({ allowPollReset: true, expectedRevision: 'rev1' });
        else expect(result.reason).toBe('cancelled');
    });
    it('cancels an accepted reset when the account changes while the prompt is open', async () => {
        let session = 1, approve;
        const change = vi.fn(async () => ({ ok: false, reason: 'poll_has_votes' }));
        const edit = compile('_savePodRoomEdit', {
            _webRoomSessionId: () => session, messageMap: { poll: { content: 'Original' } },
            activeView: { id: 'room', type: 'local_room' }, _changePodRoomMessage: change,
            showConfirm: (_text, yes) => { approve = yes; }, t: key => key,
        });
        const pending = edit({ id: 'room', type: 'local_room' }, 'poll', 'Revised', 'rev1');
        await Promise.resolve(); session++; approve();
        expect(await pending).toMatchObject({ ok: false, reason: 'cancelled' });
        expect(change).toHaveBeenCalledOnce();
    });
});
