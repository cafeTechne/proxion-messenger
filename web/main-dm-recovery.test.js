import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
function handler(name, deps) {
    const start = new RegExp(`(?:async )?function ${name}\\(`).exec(source).index;
    const tail = source.slice(start), end = /^        }\r?$/m.exec(tail);
    return new Function(...Object.keys(deps), `${tail.slice(0, end.index + end[0].length)}; return ${name};`)(...Object.values(deps));
}
describe('legacy send review in the composition root', () => {
    it('copies only after consent, leaving encryption and message id creation to a new Send gesture', async () => {
        let consent;
        const input = { value: '', dispatchEvent: vi.fn(), focus: vi.fn() };
        const deps = { _dmOutbox: { send: vi.fn() }, _outgoingScope: () => 'alice/device', authContext: () => ({ epoch: 1 }),
            showConfirm: vi.fn((text, yes) => { consent = yes; }), t: key => key,
            document: { getElementById: () => input }, activeView: { id: 'bob-thread' }, showToast: vi.fn() };
        const review = handler('_resendStoredDm', deps);
        await review({ thread_id: 'bob-thread', message_id: 'old-id', content: 'original text' });
        expect(input.value).toBe(''); expect(deps._dmOutbox.send).not.toHaveBeenCalled();
        consent(); expect(input.value).toBe('original text'); expect(input.focus).toHaveBeenCalled();
    });
    it('does not overwrite another draft or apply a delayed prompt after account replacement', async () => {
        let consent, epoch = 1;
        const input = { value: 'new draft' }, toast = vi.fn();
        const deps = { _dmOutbox: {}, _outgoingScope: () => 'alice', authContext: () => ({ epoch }),
            showConfirm: (text, yes) => { consent = yes; }, t: key => key,
            document: { getElementById: () => input }, activeView: { id: 'bob' }, showToast: toast };
        const review = handler('_resendStoredDm', deps);
        await review({ thread_id: 'bob', content: 'old text' }); consent();
        expect(input.value).toBe('new draft'); expect(toast).toHaveBeenCalledWith('outbox.openOriginal');
        input.value = ''; await review({ thread_id: 'bob', content: 'old text' }); epoch++; consent();
        expect(input.value).toBe('');
    });
    it('registers history-only rows for manual review without sending automatically', async () => {
        const rec = { from_webid: 'alice', message_id: 'legacy', thread_id: 'bob', content: 'text' };
        const deps = { dmHistoryUndelivered: async () => [rec], selfWebId: 'alice', _undeliveredDms: new Map(),
            sendStatus: { track: vi.fn() }, _resendStoredDm: vi.fn() };
        await handler('_restoreUndeliveredDms', deps)();
        expect(deps.sendStatus.track).toHaveBeenCalledWith('legacy', expect.any(Function), { automatic: false });
        expect(deps._resendStoredDm).not.toHaveBeenCalled();
    });
});
it('pauses unknown or revoked frozen devices without adding a newly linked device', () => {
    const send = vi.fn(), rosters = {}, requested = new Map();
    const inspect = handler('_frozenDmPlanStatus', { _peerDeviceKeys: rosters, _rosterRequested: requested,
        socket: { readyState: 1, send }, WebSocket: { OPEN: 1 }, cachedPeerPub: () => 'pub' });
    const job = { entries: [{ account: 'bob', device: 'original', pub: 'pub' }] };
    expect(inspect(job)).toBe('waiting'); expect(inspect(job)).toBe('waiting'); expect(send).toHaveBeenCalledTimes(1);
    rosters.bob = [{ device_id: 'original', pub_b64u: 'pub' }, { device_id: 'new', pub_b64u: 'new-pub' }];
    expect(inspect(job)).toBe('ready'); expect(job.entries).toHaveLength(1);
    rosters.bob = []; expect(inspect(job)).toBe('changed');
});

it('waits for own-device discovery rather than freezing an empty mirror plan immediately after reload', async () => {
    const rosters = {}, own = [{ device_id: 'sibling', pub_b64u: 'key' }], send = vi.fn();
    const wait = vi.fn(async () => { rosters.alice = own; return own; });
    const discover = handler('_ownDevicesForSend', { transport: { mode: 'gateway' }, isDmPeerWebId: () => false,
        _peerDeviceKeys: rosters, selfWebId: 'alice', socket: { readyState: 1, send }, WebSocket: { OPEN: 1 },
        _deviceKeyWaiter: { wait }, identity: { ownOtherDevices: () => rosters.alice || [] } });
    expect(await discover('bob')).toEqual(own); expect(wait).toHaveBeenCalledWith('alice'); expect(send).toHaveBeenCalledTimes(1);
    expect(await discover('bob')).toEqual(own); expect(send).toHaveBeenCalledTimes(1);
});
