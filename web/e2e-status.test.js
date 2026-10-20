import { describe, it, expect, vi, beforeEach } from 'vitest';

let e2eEnabled, supported, verifiedPeers;
vi.mock('./e2e.js', () => ({
  get e2eSupported() { return supported; },
  isE2EEnabled: (id) => e2eEnabled.has(id),
  isPeerVerified: (id) => verifiedPeers.has(id),
  myX25519PubB64u: () => 'MYPUBKEYbase64url0000',
  safetyNumber: async () => '12345 67890 11111',
  e2eScopedKey: (k) => k,
}));

import { createE2EStatus } from './e2e-status.js';

let els, store;
function mkEl(over = {}) {
  const classes = new Set();
  const classList = { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) };
  return { style: {}, dataset: {}, classList, textContent: '', innerHTML: '', title: '', value: '', disabled: false, ...over };
}
beforeEach(() => {
  els = {};
  store = {};
  e2eEnabled = new Set();
  verifiedPeers = new Set();
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
    expect(els['dm-e2e-status'].dataset.state).toBe('encrypted');
  });
  it('shows a verified contact as verified and drops the Verify button', () => {
    e2eEnabled.add('did:key:zAlice');
    verifiedPeers.add('did:key:zAlice');
    els['dm-e2e-status'] = mkEl();
    els['dm-e2e-verify-btn'] = mkEl();
    createE2EStatus()._updateE2EStatus('did:key:zAlice');
    expect(els['dm-e2e-status'].dataset.state).toBe('verified');
    expect(els['dm-e2e-status'].title).toBe('e2e.tip.verified');
    expect(els['dm-e2e-verify-btn'].style.display).toBe('none');
  });
  it('shows "No E2E" when supported but key not exchanged', () => {
    els['dm-e2e-status'] = mkEl();
    createE2EStatus()._updateE2EStatus('did:key:zBob');
    expect(els['dm-e2e-status'].innerHTML).toContain('<span class="e2e-label">e2e.none</span>');
    // Styled neutral, not with the encrypted state's green.
    expect(els['dm-e2e-status'].dataset.state).toBe('none');
  });
  it('says "unencrypted" when the user opted this keyless peer in to plaintext', () => {
    store['proxion_dm_plaintext_ok_did:key:zBot'] = '1';
    els['dm-e2e-status'] = mkEl();
    createE2EStatus()._updateE2EStatus('did:key:zBot');
    expect(els['dm-e2e-status'].textContent).toBe('e2e.plain');
    expect(els['dm-e2e-status'].style.display).toBe('inline');
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
  it('hides the bar until both encryption keys are known', async () => {
    const e = createE2EStatus();
    await e._updateIdentityFingerprint('did:key:zCarol');   // no peer key yet
    expect(els['fingerprint-bar'].style.display).toBe('none');
    await e._updateIdentityFingerprint(null);
    expect(els['fingerprint-bar'].style.display).toBe('none');
    expect(e.state._fingerprintBarDid).toBeNull();
  });
  it('shows the same number the Verify dialog shows, computed from the two keys', async () => {
    store['proxion_e2e_peer_pub_did:key:zCarol'] = 'CAROLPUB';
    const e = createE2EStatus();
    await e._updateIdentityFingerprint('did:key:zCarol');
    expect(els['fingerprint-bar'].style.display).toBe('flex');
    expect(els['fingerprint-words'].textContent).toBe('12345 67890 11111');   // safetyNumber()
    expect(e.state._fingerprintBarDid).toBe('did:key:zCarol');
    expect(els['fingerprint-verify-btn'].disabled).toBe(false);
    expect(els['fingerprint-verify-btn'].textContent).toContain('e2e.markVerified');
  });
  it('marks the button done for a verified contact', async () => {
    store['proxion_e2e_peer_pub_did:key:zCarol'] = 'CAROLPUB';
    verifiedPeers.add('did:key:zCarol');
    const e = createE2EStatus();
    await e._updateIdentityFingerprint('did:key:zCarol');
    expect(els['fingerprint-verify-btn'].disabled).toBe(true);
    expect(els['fingerprint-verify-btn'].textContent).toContain('e2e.verified');
    // The verified look is a class in style.css, not inline colors.
    expect(els['fingerprint-verify-btn'].classList.contains('is-verified')).toBe(true);
    expect(els['fingerprint-verify-btn'].style.background).toBeUndefined();
  });
  it('ignores the old gateway-computed mark', async () => {
    store['proxion_e2e_peer_pub_did:key:zCarol'] = 'CAROLPUB';
    store['proxion_verified_did:key:zCarol'] = '1';
    const e = createE2EStatus();
    await e._updateIdentityFingerprint('did:key:zCarol');
    expect(els['fingerprint-verify-btn'].disabled).toBe(false);
    expect(els['fingerprint-verify-btn'].classList.contains('is-verified')).toBe(false);
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
