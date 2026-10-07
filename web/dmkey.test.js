import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'fs';

const _ls = {};
globalThis.localStorage = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null),
    setItem: (k, v) => { _ls[k] = String(v); },
    removeItem: (k) => { delete _ls[k]; },
};
vi.mock('./e2e.js', () => ({ e2eScopedKey: (k) => k }));

import {
    resolveDmSendMode, isPlaintextOptedIn, setPlaintextOptIn, primaryKeyFromDevices,
    createDeviceKeyWaiter, DmKeyMissingError,
} from './dmkey.js';

beforeEach(() => { for (const k in _ls) delete _ls[k]; });

describe('resolveDmSendMode', () => {
    it('encrypts when a key is cached, without fetching', async () => {
        const obtainKey = vi.fn();
        expect(await resolveDmSendMode('bob', { hasKey: () => true, obtainKey })).toBe('encrypt');
        expect(obtainKey).not.toHaveBeenCalled();
    });

    it('fetches a missing key and then encrypts', async () => {
        const keys = new Set();
        const obtainKey = vi.fn(async (id) => { keys.add(id); });
        expect(await resolveDmSendMode('bob', { hasKey: (id) => keys.has(id), obtainKey })).toBe('encrypt');
        expect(obtainKey).toHaveBeenCalledWith('bob');
    });

    it('refuses to send plaintext when no key can be obtained', async () => {
        const obtainKey = vi.fn(async () => { throw new Error('offline'); });
        expect(await resolveDmSendMode('bob', { hasKey: () => false, obtainKey })).toBe('refuse');
    });

    it('refuses with no peer id at all', async () => {
        expect(await resolveDmSendMode(null, { hasKey: () => false, obtainKey: async () => {} })).toBe('refuse');
    });

    it('sends plaintext only after an explicit per-peer opt-in', async () => {
        setPlaintextOptIn('bot', true);
        const opts = { hasKey: () => false, obtainKey: async () => {} };
        expect(await resolveDmSendMode('bot', opts)).toBe('plaintext');
        expect(await resolveDmSendMode('bob', opts)).toBe('refuse');
    });

    it('prefers encryption once an opted-in peer publishes a key', async () => {
        setPlaintextOptIn('bot', true);
        expect(await resolveDmSendMode('bot', { hasKey: () => true, obtainKey: async () => {} })).toBe('encrypt');
    });
});

describe('plaintext opt-in storage', () => {
    it('round-trips and clears', () => {
        expect(isPlaintextOptedIn('p')).toBe(false);
        setPlaintextOptIn('p', true);
        expect(isPlaintextOptedIn('p')).toBe(true);
        setPlaintextOptIn('p', false);
        expect(isPlaintextOptedIn('p')).toBe(false);
    });
});

describe('primaryKeyFromDevices', () => {
    it('picks the roster entry recorded under the account id', () => {
        const devs = [{ device_id: 'did:key:zDev2', pub_b64u: 'D2' }, { device_id: 'did:key:zAcct', pub_b64u: 'ACCT' }];
        expect(primaryKeyFromDevices('did:key:zAcct', devs)).toBe('ACCT');
    });
    it('returns null without a matching entry', () => {
        expect(primaryKeyFromDevices('did:key:zAcct', [{ device_id: 'did:key:zDev2', pub_b64u: 'D2' }])).toBe(null);
        expect(primaryKeyFromDevices('did:key:zAcct', null)).toBe(null);
    });
});

describe('createDeviceKeyWaiter', () => {
    it('resolves waiters for the peer with the devices', async () => {
        const w = createDeviceKeyWaiter();
        const p = w.wait('bob', 1000);
        w.resolve('bob', [{ device_id: 'bob', pub_b64u: 'K' }]);
        expect(await p).toEqual([{ device_id: 'bob', pub_b64u: 'K' }]);
    });
    it('settles with null on timeout', async () => {
        const w = createDeviceKeyWaiter();
        expect(await w.wait('bob', 5)).toBe(null);
    });
});

describe('DmKeyMissingError', () => {
    it('carries the peer id', () => {
        const e = new DmKeyMissingError('bob');
        expect(e.peerId).toBe('bob');
        expect(e.name).toBe('DmKeyMissingError');
    });
});

// The composer's DM branches must go through the key gate; the plain-path send
// itself is only reached for a keyed peer or an explicit opt-in.
describe('main.js send path', () => {
    const src = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
    const body = src.slice(src.indexOf('async function _sendComposed('), src.indexOf('// Optimistic render: show the message instantly'));
    it('gates both DM branches on _requireDmKey before building the payload', () => {
        expect(body).toMatch(/await _requireDmKey\(peerWebid, [^)]*\);\s*payload = \{\s*cmd: "local_dm"/);
        expect(body).toMatch(/if \(activeView\.type === "dm"\) await _requireDmKey\(/);
    });
    it('no longer documents a plaintext fallback for the first message', () => {
        expect(body).not.toMatch(/fall back to plaintext because isE2EEnabled/);
    });
});
