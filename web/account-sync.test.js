import { describe, it, expect, beforeEach } from 'vitest';
import { createAccountSync, SYNCED_PREFS } from './account-sync.js';

function memStorage(init = {}) {
    const m = new Map(Object.entries(init));
    return {
        getItem: (k) => (m.has(k) ? m.get(k) : null),
        setItem: (k, v) => m.set(k, String(v)),
        removeItem: (k) => m.delete(k),
        _m: m,
    };
}

const ACCOUNT = 'did:key:zAccount';

function setup(init = {}, muted = []) {
    const storage = memStorage(init);
    const sent = [];
    const mutedThreads = new Set(muted);
    const names = [];
    const prefChanges = [];
    const serverMutes = [];
    const sync = createAccountSync({
        send: (p) => sent.push(p),
        storage,
        getMutedThreads: () => mutedThreads,
        applyMute: (tid, m) => { if (m) mutedThreads.add(tid); else mutedThreads.delete(tid); },
        sendMute: (tid, m) => serverMutes.push([tid, m]),
        onName: (n) => names.push(n),
        onPrefs: (keys) => prefChanges.push(keys),
    });
    sync.setAccount(ACCOUNT);
    return { sync, storage, sent, mutedThreads, names, prefChanges, serverMutes };
}

describe('account sync (linked devices)', () => {
    let s;
    beforeEach(() => { s = setup(); });

    it('a fresh linked device takes the account name, mutes and settings', () => {
        s.sync.apply({
            type: 'account_sync', full: true, webid: ACCOUNT, display_name: 'Alice',
            prefs: { proxion_theme: 'dark', proxion_receipts_enabled: '0' },
            muted_threads: ['room-1', 'dm-2'],
        });
        expect(s.storage.getItem('proxion_display_name')).toBe('Alice');
        expect(s.names).toEqual(['Alice']);
        expect(s.storage.getItem('proxion_theme')).toBe('dark');
        expect(s.storage.getItem('proxion_receipts_enabled')).toBe('0');
        expect(s.prefChanges[0].sort()).toEqual(['proxion_receipts_enabled', 'proxion_theme']);
        expect([...s.mutedThreads].sort()).toEqual(['dm-2', 'room-1']);
    });

    it('ignores events for any account other than the one this socket registered as', () => {
        expect(s.sync.apply({ type: 'account_sync', webid: 'did:key:zOther', display_name: 'Mallory' })).toBe(false);
        expect(s.sync.apply({ type: 'account_sync', display_name: 'Mallory' })).toBe(false);
        const unset = setup();
        unset.sync.setAccount(null);
        expect(unset.sync.apply({ type: 'account_sync', webid: ACCOUNT, display_name: 'X' })).toBe(false);
        expect(s.storage.getItem('proxion_display_name')).toBe(null);
    });

    it('only allowlisted, short settings are written', () => {
        s.sync.apply({
            type: 'account_sync', webid: ACCOUNT,
            prefs: { proxion_gateway_http_url: 'https://evil', proxion_locale: 'x'.repeat(100), proxion_theme: 3 },
        });
        expect(s.storage._m.size).toBe(0);
        expect(s.prefChanges).toEqual([]);
    });

    it('applies live deltas: rename, mute, unmute, cleared pref', () => {
        const d = setup({ proxion_theme: 'light' }, ['room-1']);
        d.sync.apply({ type: 'account_sync', webid: ACCOUNT, display_name: 'Alice B' });
        d.sync.apply({ type: 'account_sync', webid: ACCOUNT, mutes: { 'room-1': false, 'dm-9': true } });
        d.sync.apply({ type: 'account_sync', webid: ACCOUNT, prefs: { proxion_theme: '' } });
        expect(d.storage.getItem('proxion_display_name')).toBe('Alice B');
        expect([...d.mutedThreads]).toEqual(['dm-9']);
        expect(d.storage.getItem('proxion_theme')).toBe(null);
        expect(d.prefChanges).toEqual([['proxion_theme']]);
    });

    it('the first snapshot seeds the account from this device, later ones follow the account', () => {
        const d = setup({ proxion_display_name: 'Alice', proxion_locale: 'de' }, ['room-1']);
        d.sync.apply({ type: 'account_sync', full: true, webid: ACCOUNT, display_name: '', prefs: {}, muted_threads: ['room-2'] });
        expect(d.sent).toContainEqual({ cmd: 'set_identity', display_name: 'Alice' });
        expect(d.sent).toContainEqual({ cmd: 'set_account_pref', key: 'proxion_locale', value: 'de' });
        expect(d.serverMutes).toEqual([['room-1', true]]);
        expect([...d.mutedThreads].sort()).toEqual(['room-1', 'room-2']);
        // Later: room-1 was unmuted on another device while this one was away.
        d.sync.apply({ type: 'account_sync', full: true, webid: ACCOUNT, display_name: 'Alice',
            prefs: { proxion_locale: 'de' }, muted_threads: ['room-2'] });
        expect([...d.mutedThreads]).toEqual(['room-2']);
    });

    it('pushPref sends only allowlisted keys, an unset value as empty', () => {
        s.sync.pushPref('proxion_theme');
        s.sync.pushPref('proxion_gateway_http_url');
        expect(s.sent).toEqual([{ cmd: 'set_account_pref', key: 'proxion_theme', value: '' }]);
        expect(SYNCED_PREFS).not.toContain('proxion_gateway_http_url');
    });
});
