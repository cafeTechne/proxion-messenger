import { describe, it, expect, vi, beforeEach } from 'vitest';

let e2eEnabled, supported;
vi.mock('./e2e.js', () => ({
  get e2eSupported() { return supported; },
  isE2EEnabled: (id) => e2eEnabled.has(id),
  myX25519PubB64u: () => 'MYPUBKEYbase64url0000',
  safetyNumber: async () => '12345 67890 11111',
}));

import { createE2EStatus } from './e2e-status.js';

let els, store;
function mkEl(over = {}) {
  return { style: {}, textContent: '', innerHTML: '', title: '', value: '', disabled: false, ...over };
}
beforeEach(() => {
  els = {};
  store = {};
  e2eEnabled = new Set();
  supported = true;
  global.document = { getElementById: (id) => (id in els ? els[id] : null) };
  global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
});

describe('_updateE2EStatus', () => {
  it('shows the E2E badge for an encrypted peer', () => {
    e2eEnabled.add('did:key:zAlice');
    els['dm-e2e-status'] = mkEl({ style: { display: 'none' } });
    els['dm-e2e-verify-btn'] = mkEl({ style: { display: 'none' } });
    createE2EStatus()._updateE2EStatus('did:key:zAlice');
    expect(els['dm-e2e-status'].style.display).toBe('inline');
    expect(els['dm-e2e-status'].innerHTML).toContain('E2E');
    expect(els['dm-e2e-verify-btn'].style.display).toBe('inline');
  });
  it('shows "No E2E" when supported but key not exchanged', () => {
    els['dm-e2e-status'] = mkEl();
    createE2EStatus()._updateE2EStatus('did:key:zBob');
    expect(els['dm-e2e-status'].textContent).toBe('No E2E');
  });
  it('hides the badge entirely with no peer', () => {
    els['dm-e2e-status'] = mkEl({ style: { display: 'inline' } });
    createE2EStatus()._updateE2EStatus(null);
    expect(els['dm-e2e-status'].style.display).toBe('none');
  });
});

describe('_updateIdentityFingerprint', () => {
  beforeEach(() => {
    els['fingerprint-bar'] = mkEl();
    els['fingerprint-words'] = mkEl();
    els['fingerprint-verify-btn'] = mkEl();
  });
  it('hides the bar and clears state for a non did:key peer', async () => {
    const e = createE2EStatus();
    e.state._fingerprintBarDid = 'stale';
    await e._updateIdentityFingerprint('not-a-did');
    expect(els['fingerprint-bar'].style.display).toBe('none');
    expect(e.state._fingerprintBarDid).toBeNull();
  });
  it('renders safety words and records the shown DID', async () => {
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ safety_words: ['red','sun','tree','owl','blue','fish'] }) }));
    const e = createE2EStatus();
    await e._updateIdentityFingerprint('did:key:zCarol');
    expect(els['fingerprint-bar'].style.display).toBe('flex');
    expect(els['fingerprint-words'].textContent).toContain('red sun tree');
    expect(e.state._fingerprintBarDid).toBe('did:key:zCarol');
    expect(els['fingerprint-verify-btn'].disabled).toBe(false);
  });
  it('marks the verify button done when already verified', async () => {
    store['proxion_verified_did:key:zCarol'] = '1';
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ safety_words: ['a','b','c'] }) }));
    const e = createE2EStatus();
    await e._updateIdentityFingerprint('did:key:zCarol');
    expect(els['fingerprint-verify-btn'].disabled).toBe(true);
    expect(els['fingerprint-verify-btn'].textContent).toContain('Verified');
  });
});

describe('_openVerifyModal', () => {
  it('fills key fields + safety number and opens the modal', async () => {
    store['proxion_e2e_peer_pub_did:key:zDan'] = 'THEIRPUBKEYbase64url9999';
    els['e2e-verify-modal'] = mkEl({ style: { display: 'none' } });
    els['e2e-modal-my-key'] = mkEl();
    els['e2e-modal-their-key'] = mkEl();
    els['e2e-modal-safety-number'] = mkEl();
    els['e2e-modal-current-peer'] = mkEl();
    await createE2EStatus()._openVerifyModal('did:key:zDan');
    expect(els['e2e-verify-modal'].style.display).toBe('flex');
    expect(els['e2e-modal-safety-number'].textContent).toBe('12345 67890 11111');
    expect(els['e2e-modal-current-peer'].value).toBe('did:key:zDan');
  });
  it('does nothing without the peer public key', async () => {
    els['e2e-verify-modal'] = mkEl({ style: { display: 'none' } });
    await createE2EStatus()._openVerifyModal('did:key:zNobody');
    expect(els['e2e-verify-modal'].style.display).toBe('none');
  });
});
