import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  createInvite, contactAddressFromInput, contactInputKind, isLoopbackUrl, addressIsOnHost,
  stashPendingJoin, readPendingJoin, PENDING_JOIN_KEY, PENDING_JOIN_MAX_AGE_MS,
} from './invite.js';

let store, search, replaced, socketOpen, captured;
beforeEach(() => {
  store = {};
  search = '';
  replaced = [];
  socketOpen = true;
  captured = [];
  global.WebSocket = { OPEN: 1 };
  global.window = { location: { get search() { return search; }, pathname: '/' } };
  global.history = { replaceState: (a, b, url) => replaced.push(url) };
  global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
});

function make() {
  return createInvite({
    getSocket: () => (socketOpen ? { readyState: 1 } : { readyState: 3 }),
    onPendingInvite: (addr) => captured.push(addr),
  });
}

describe('capturePendingInvite', () => {
  it('stashes ?from= into localStorage and cleans the URL', () => {
    search = '?from=did%3Akey%3AzBob%40https%3A%2F%2Fgw';
    const addr = make().capturePendingInvite();
    expect(addr).toBe('did:key:zBob@https://gw');
    expect(store['proxion_pending_invite']).toBe('did:key:zBob@https://gw');
    expect(replaced).toContain('/');
  });
  it('unwraps a full invite URL passed as ?from=', () => {
    search = '?from=' + encodeURIComponent('https://gw.example/invite?from=' + encodeURIComponent('did:key:zA@https://gw'));
    const addr = make().capturePendingInvite();
    expect(addr).toBe('did:key:zA@https://gw');
  });
  it('returns null and does nothing when no invite param is present', () => {
    search = '?foo=bar';
    expect(make().capturePendingInvite()).toBeNull();
    expect(store['proxion_pending_invite']).toBeUndefined();
  });
});

describe('consumePendingInvite', () => {
  it('hands the stored invite to onPendingInvite and clears it once the socket is open', () => {
    store['proxion_pending_invite'] = 'did:key:zBob@https://gw';
    const inv = make();
    const addr = inv.consumePendingInvite();
    expect(addr).toBe('did:key:zBob@https://gw');
    expect(captured).toEqual(['did:key:zBob@https://gw']);
    expect(store['proxion_pending_invite']).toBeUndefined(); // cleared
    // idempotent — a second registered event does nothing
    expect(inv.consumePendingInvite()).toBeNull();
    expect(captured).toHaveLength(1);
  });
  it('keeps the invite for a later retry when the socket is not yet open', () => {
    store['proxion_pending_invite'] = 'did:key:zBob@https://gw';
    socketOpen = false;
    const inv = make();
    expect(inv.consumePendingInvite()).toBeNull();
    expect(store['proxion_pending_invite']).toBe('did:key:zBob@https://gw'); // retained
    expect(captured).toHaveLength(0);
    // becomes consumable once connected
    socketOpen = true;
    expect(inv.consumePendingInvite()).toBe('did:key:zBob@https://gw');
  });
  it('is a no-op when there is no pending invite', () => {
    expect(make().consumePendingInvite()).toBeNull();
  });
});

describe('contactAddressFromInput', () => {
  const addr = 'did:key:zBob@https://gw.example:8443';
  it('passes a bare address through', () => {
    expect(contactAddressFromInput(` ${addr} `)).toEqual({ address: addr, shortLink: false });
  });
  it('reads ?from= from https and proxion:// links', () => {
    const q = encodeURIComponent(addr);
    expect(contactAddressFromInput(`https://gw.example/invite?from=${q}`).address).toBe(addr);
    expect(contactAddressFromInput(`proxion://invite?from=${q}`).address).toBe(addr);
    const nested = encodeURIComponent(`https://gw.example/invite?from=${q}`);
    expect(contactAddressFromInput(`https://gw.example/?from=${nested}`).address).toBe(addr);
  });
  it('flags a short link for the gateway to resolve', () => {
    expect(contactAddressFromInput('https://gw.example:8443/i/0123456789abcdef'))
      .toEqual({ address: 'https://gw.example:8443/i/0123456789abcdef', shortLink: true });
  });
  it('leaves other URLs alone', () => {
    expect(contactAddressFromInput('https://gw.example/other').shortLink).toBe(false);
  });
});

describe('addressIsOnHost', () => {
  it('matches the gateway host and port only', () => {
    expect(addressIsOnHost('did:key:zA@https://127.0.0.1:8080', '127.0.0.1:8080')).toBe(true);
    expect(addressIsOnHost('did:key:zA@https://127.0.0.1:8081', '127.0.0.1:8080')).toBe(false);
    expect(addressIsOnHost('garbage', '127.0.0.1:8080')).toBe(false);
  });
});

describe('pending join stash', () => {
  it('returns a recent code and drops a stale or undated one', () => {
    stashPendingJoin(localStorage, 'ABC', 1000);
    expect(readPendingJoin(localStorage, 1000 + 60_000)).toBe('ABC');
    expect(readPendingJoin(localStorage, 1000 + PENDING_JOIN_MAX_AGE_MS + 1)).toBe('');
    expect(store[PENDING_JOIN_KEY]).toBeUndefined();
    store[PENDING_JOIN_KEY] = 'OLDCODE';
    expect(readPendingJoin(localStorage)).toBe('');
    expect(store[PENDING_JOIN_KEY]).toBeUndefined();
  });
});

describe('contactInputKind', () => {
  it('tells a Solid WebID from a Proxion desktop address or link', () => {
    expect(contactInputKind('https://alice.solidcommunity.net/profile/card#me')).toBe('webid');
    expect(contactInputKind('http://localhost:3000/alice/profile/card#me')).toBe('webid');
    expect(contactInputKind('did:key:z6MkAbc@https://gw.example')).toBe('proxion');
    expect(contactInputKind('did:key:z6MkAbc')).toBe('proxion');
    expect(contactInputKind('proxion://invite?from=did%3Akey%3Az6Mk')).toBe('proxion');
    expect(contactInputKind('https://gw.example/invite?from=did%3Akey%3Az6Mk')).toBe('proxion');
    expect(contactInputKind('https://gw.example:8080/i/645ad6afb53f32ba')).toBe('proxion');
    expect(contactInputKind('https://gw.example:8080/?join=abc')).toBe('proxion');
    expect(contactInputKind('https://gw.example:8080/')).toBe('invalid');
    expect(contactInputKind('bob')).toBe('invalid');
    expect(contactInputKind('')).toBe('invalid');
  });
});

describe('isLoopbackUrl', () => {
  it('flags links that only open on this computer', () => {
    expect(isLoopbackUrl('http://127.0.0.1:8080/?join=x')).toBe(true);
    expect(isLoopbackUrl('http://localhost:8080/?join=x')).toBe(true);
    expect(isLoopbackUrl('https://abc.trycloudflare.com/?join=x')).toBe(false);
    expect(isLoopbackUrl('abc')).toBe(false);
  });
});
