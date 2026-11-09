import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import {
    createGatewayTransport, createPodTransport, supportsConversationFeature,
    applyTransportGating, applyConversationFeatureGating, gatedRootClasses,
} from './transport.js';
import { canScheduleInView } from './dmkey.js';
import { isDmPeerWebId } from './webdm.js';

// Exercise the composition root's real functions without booting the browser UI.
const source = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
function declaration(name) {
    const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
    if (!match) throw new Error(`Missing handler: ${name}`);
    const tail = source.slice(match.index);
    const end = /^        }\r?$/m.exec(tail);
    if (!end) throw new Error(`Missing closing brace: ${name}`);
    return tail.slice(0, end.index + end[0].length);
}

function harness({ mode = 'gateway', view = { type: 'local_room', id: 'gateway-room' }, remote = false, desktopPod = false, keyed = false, plaintext = false } = {}) {
    const classes = new Set();
    const button = { attrs: {}, style: {},
        setAttribute(k, v) { this.attrs[k] = v; },
        removeAttribute(k) { delete this.attrs[k]; } };
    const picker = { style: { display: 'block' } };
    const input = { value: 'Keep this draft' };
    const datetime = { value: '2099-12-31T12:00' };
    const elements = { 'schedule-btn': button, 'schedule-picker': picker,
        'message-input': input, 'schedule-datetime-input': datetime };
    const doc = { getElementById: (id) => elements[id] || null,
        documentElement: { classList: {
            add: (c) => classes.add(c),
            toggle: (c, on) => on ? classes.add(c) : classes.delete(c),
        } } };
    const transport = mode === 'web' ? createPodTransport()
        : createGatewayTransport({ connection: { socketSendOrQueue() {} } });
    const listeners = {};
    const deps = {
        activeView: view, transport,
        _remoteRooms: remote ? { [view.id]: {} } : {},
        crossRooms: { isPodRoom: (id) => desktopPod && id === view.id },
        isDmPeerWebId, supportsConversationFeature,
        applyConversationFeatureGating: (transport, options) => applyConversationFeatureGating(transport, options, doc),
        canScheduleInView: (v, opts) => canScheduleInView(v, { ...opts, optedIn: () => plaintext }),
        isE2EEnabled: () => keyed,
        identity: { devicesForPeer: () => [] },
        t: (key) => key, document: doc,
        attachListener: (selector, event, fn) => { listeners[selector] = fn; },
        showToast: vi.fn(), socket: {},
        scheduledComposer: { submit: vi.fn() },
    };
    const confirmStart = source.indexOf("            attachListener('#schedule-confirm-btn'");
    const confirmEnd = source.indexOf("            attachListener('#schedule-cancel-btn'", confirmStart);
    if (confirmStart < 0 || confirmEnd < 0) throw new Error('Missing scheduling listeners');
    const helpers = ['_isPodRoomView', '_conversationSupports', '_applyCrossModeThreadGating', '_scheduleAllowed', '_scheduleUnavailableText', '_syncScheduleBtn'];
    const compiled = `${helpers.map(declaration).join('\n')}\n${source.slice(confirmStart, confirmEnd)}\nreturn { ${helpers.join(', ')}, setView(v) { activeView = v; } };`;
    const app = new Function(...Object.keys(deps), compiled)(...Object.values(deps));
    return { ...app, deps, classes, button, picker, input, listeners, transport, doc };
}

const crossDm = { type: 'local_dm', id: 'https://peer.example/profile/card#me' };
const cases = [
    ['gateway room', {}, true],
    ['gateway DM', { view: { type: 'local_dm', id: 'dm-cert', peerDid: 'did:key:zPeer' }, plaintext: true }, true],
    ['browser room', { mode: 'web' }, false],
    ['joined pod room', { remote: true }, false],
    ['desktop pod room', { desktopPod: true }, false],
    ['cross-mode DM', { view: crossDm }, false],
];

describe('main conversation capabilities', () => {
    it.each(cases)('uses the actual protocol for %s', (_name, options, supported) => {
        const h = harness(options);
        for (const feature of ['schedule']) {
            expect(h._conversationSupports(feature)).toBe(supported);
        }
        expect(h._conversationSupports('polls')).toBe(supported || ['browser room', 'joined pod room', 'desktop pod room'].includes(_name));
        expect(h._conversationSupports('history')).toBe(true);
        expect(h._scheduleAllowed()).toBe(supported);
    });

    it('enables pod room changes while preserving other browser feature gates', () => {
        const h = harness({ mode: 'web' });
        applyTransportGating(h.transport, h.doc);
        const expected = new Set(gatedRootClasses(h.transport));
        expect(h.classes).toEqual(expected);
        h._applyCrossModeThreadGating();
        h.setView(crossDm);
        h._applyCrossModeThreadGating();
        h.setView({ type: 'local_room', id: 'other' });
        h._applyCrossModeThreadGating();
        expected.delete('gate-no-edit');
        expected.delete('gate-no-delete');
        expected.delete('gate-no-reactions');
        expected.delete('gate-no-pins');
        expected.delete('gate-no-polls');
        expected.add('gate-pod-room');
        expect(h.classes).toEqual(expected);
        expect(h._conversationSupports('edit')).toBe(true);
        expect(h._conversationSupports('delete')).toBe(true);
    });

    it('restores gateway controls after leaving a desktop pod conversation', () => {
        const h = harness({ desktopPod: true });
        h._applyCrossModeThreadGating();
        h._syncScheduleBtn();
        expect(h.classes.has('gate-pod-room')).toBe(true);
        expect(h.button.attrs['aria-disabled']).toBe('true');
        expect(h.button.title).toBe('schedule.unavailable');
        expect(h.picker.style.display).toBe('none');
        h.setView({ type: 'local_room', id: 'another-gateway-room' });
        h._applyCrossModeThreadGating();
        h._syncScheduleBtn();
        expect(h.classes.has('gate-crossmode')).toBe(false);
        expect(h.classes.has('gate-no-reactions')).toBe(false);
        expect(h.button.attrs['aria-disabled']).toBeUndefined();
        expect(h.button.title).toBe('ui.scheduleMessage');
    });

    it('keeps the encryption restriction when the gateway supports scheduling', () => {
        const h = harness({ view: { type: 'local_dm', id: 'dm-cert', peerDid: 'did:key:zPeer' }, keyed: true, plaintext: true });
        expect(h._conversationSupports('schedule')).toBe(true);
        expect(h._scheduleAllowed()).toBe(false);
        h._syncScheduleBtn();
        expect(h.button.title).toBe('schedule.e2eUnavailable');
        h.listeners['#schedule-confirm-btn']();
        expect(h.deps.scheduledComposer.submit).not.toHaveBeenCalled();
        expect(h.input.value).toBe('Keep this draft');
    });
});

describe('main schedule confirmation guard', () => {
    it.each(cases.filter(([, , supported]) => !supported))('preserves the draft and rejects a direct confirmation in %s', (_name, options) => {
        const h = harness(options);
        h.listeners['#schedule-confirm-btn']();
        expect(h.deps.scheduledComposer.submit).not.toHaveBeenCalled();
        expect(h.input.value).toBe('Keep this draft');
        expect(h.deps.showToast).toHaveBeenCalledWith('schedule.unavailable');
    });

    it('hands a supported gateway request to the acknowledgement-aware workflow', () => {
        const h = harness();
        h.listeners['#schedule-confirm-btn']();
        expect(h.deps.scheduledComposer.submit).toHaveBeenCalledOnce();
        const command = h.deps.scheduledComposer.submit.mock.calls[0][0];
        expect(command).toMatchObject({ thread_id: 'gateway-room', content: 'Keep this draft' });
        expect(new Date(command.send_at).getTime()).toBe(new Date('2099-12-31T12:00').getTime());
        expect(h.input.value).toBe('Keep this draft');
    });
});


describe('Members panel supplemental capabilities', () => {
    it.each([false, true])('loads the member list without unsupported custom-emoji reads (gateway support: %s)', (supported) => {
        const list = {}, panel = { style: {} };
        const socket = { send: vi.fn() }, roomEmoji = { requestList: vi.fn() };
        const deps = {
            _membersRoomId: null, window: { innerWidth: 1024 },
            document: { getElementById: (id) => id === 'members-panel' ? panel : list },
            inlineNotice: () => 'Loading', t: (key) => key, socket, roomEmoji,
            _conversationSupports: () => supported,
        };
        const showRoomMembers = new Function(...Object.keys(deps), `${declaration('showRoomMembers')}; return showRoomMembers;`)(...Object.values(deps));
        showRoomMembers('room-a');
        expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ cmd: 'get_room_members', room_id: 'room-a' }));
        expect(panel.style.display).toBe('block');
        expect(roomEmoji.requestList).toHaveBeenCalledTimes(supported ? 1 : 0);
    });
});


describe('room options follow the conversation protocol', () => {
    it.each([['gateway', true], ['browser', false], ['desktop pod', false]])('offers supported owner actions in %s rooms', (_mode, supported) => {
        const ids = ['room-opt-disappear', 'room-opt-bans', 'room-opt-edit', 'integrations-btn'];
        const elements = Object.fromEntries(ids.map((id) => [id, { hidden: false, style: {} }]));
        const deps = {
            activeView: { type: 'local_room', id: 'room-a' },
            _syncConversationOptions: () => {},
            _conversationSupports: () => supported,
            document: { getElementById: (id) => elements[id] || null },
            roomCreatorOf: new Set(['room-a']), _canAdminRoom: () => true,
            socket: { readyState: 1, send: vi.fn() }, WebSocket: { OPEN: 1 },
            voice: { updateVoiceChannels: vi.fn() }, updateDisappearBanner: () => {},
            _syncRoomOptionsBtn: () => {},
        };
        const sync = new Function(...Object.keys(deps), `${declaration('_syncDisappearOption')}; return _syncDisappearOption;`)(...Object.values(deps));
        sync();
        for (const id of ids.slice(0, 3)) expect(elements[id].hidden).toBe(!supported);
        expect(elements['integrations-btn'].style.display).toBe(supported ? 'inline-block' : 'none');
    });
});
