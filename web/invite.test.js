import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createInvite } from './invite.js';

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
