import { describe, it, expect, vi } from 'vitest';
import { createWebCalls, SIGNAL_CMDS } from './webcalls.js';

function harness({ signals = [] } = {}) {
    const events = [];
    const dropped = [];
    const deleted = [];
    const pod = {
        podEnsureCallInbox: vi.fn(async () => 'https://me/proxion/call-inbox/'),
        podDropSignal: vi.fn(async (root, signal) => { dropped.push({ root, signal }); return true; }),
        podReadSignals: vi.fn(async () => signals),
        podDeleteSignal: vi.fn(async (url) => { deleted.push(url); return true; }),
    };
    const notify = { watchResource: vi.fn(() => () => {}) };
    const calls = createWebCalls({
        pod, notify, handleEvent: (e) => events.push(e),
        getSelfWebId: () => 'https://me/profile/card#me', getDisplayName: () => 'Me',
        peerPodRoot: (w) => w.replace(/profile\/card#me$/, ''),
    });
    return { calls, pod, notify, events, dropped, deleted };
}

describe('SIGNAL_CMDS', () => {
    it('covers the voice.js signaling commands', () => {
        for (const c of ['voice_invite', 'voice_answer', 'ice_candidate', 'voice_hangup']) {
            expect(SIGNAL_CMDS.has(c)).toBe(true);
        }
    });
});

describe('createWebCalls.sendSignal', () => {
    it('drops a signal to the callee, stamped with the sender + type', async () => {
        const { calls, dropped } = harness();
        const ok = await calls.sendSignal({
            cmd: 'voice_invite', target_webid: 'https://bob.example/profile/card#me',
            sdp_offer: 'OFFER', fp_sig: 'SIG', fp_signer: 'SIGNER',
        });
        expect(ok).toBe(true);
        expect(dropped[0].root).toBe('https://bob.example/');
        expect(dropped[0].signal).toMatchObject({
            type: 'voice_invite', from_webid: 'https://me/profile/card#me',
            caller_webid: 'https://me/profile/card#me', sdp_offer: 'OFFER', fp_sig: 'SIG', fp_signer: 'SIGNER',
        });
        expect(dropped[0].signal.cmd).toBeUndefined();          // cmd renamed to type
        expect(dropped[0].signal.target_webid).toBeUndefined();  // recipient not echoed into the body
    });

    it('returns false without a target', async () => {
        const { calls, dropped } = harness();
        expect(await calls.sendSignal({ cmd: 'ice_candidate' })).toBe(false);
        expect(dropped).toHaveLength(0);
    });
});

describe('createWebCalls receive', () => {
    it('dispatches each signal as an event and deletes it', async () => {
        const { calls, events, deleted } = harness({ signals: [
            { url: 'u1', signal: { type: 'voice_invite', from_webid: 'https://bob', caller_webid: 'https://bob', sdp_offer: 'O' } },
            { url: 'u2', signal: { type: 'ice_candidate', from_webid: 'https://bob', candidate: 'C' } },
        ] });
        await calls.drainOnce();
        expect(events.map((e) => e.type)).toEqual(['voice_invite', 'ice_candidate']);
        expect(deleted).toEqual(['u1', 'u2']);
    });

    it('deletes a malformed signal without dispatching', async () => {
        const { calls, events, deleted } = harness({ signals: [{ url: 'bad', signal: { no: 'type' } }] });
        await calls.drainOnce();
        expect(events).toHaveLength(0);
        expect(deleted).toEqual(['bad']);
    });

    it('start() ensures the inbox, drains, and subscribes', async () => {
        const { calls, pod, notify } = harness();
        await calls.start();
        expect(pod.podEnsureCallInbox).toHaveBeenCalled();
        expect(pod.podReadSignals).toHaveBeenCalled();
        expect(notify.watchResource).toHaveBeenCalledWith('https://me/proxion/call-inbox/', expect.any(Function));
    });
});
