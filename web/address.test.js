import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createAddress } from './address.js';

let els, store, toasts, copyModals;
function mkEl(over = {}) {
  return { style: {}, textContent: '', innerHTML: '', title: '', ...over };
}
beforeEach(() => {
  els = {};
  store = {};
  toasts = [];
  copyModals = [];
  global.document = { getElementById: (id) => (id in els ? els[id] : null) };
  global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  global.navigator = { clipboard: { writeText: () => Promise.resolve() } };
  global.window = {};
});

function make() {
  return createAddress({
    showToast: (m) => toasts.push(m),
    showCopyModal: (t) => copyModals.push(t),
  });
}

describe('updateMyAddressBar', () => {
  it('shows a truncated did + domain and reveals the bar', () => {
    els['my-address-bar'] = mkEl({ style: { display: 'none' } });
    els['my-address-short'] = mkEl();
    els['settings-proxion-address'] = mkEl();
    const addr = createAddress({ showToast() {}, showCopyModal() {} });
    addr.updateMyAddressBar('did:key:z6MkExampleLongIdentifier@https://gw.example');
    expect(els['my-address-bar'].style.display).toBe('flex');
    expect(els['my-address-short'].textContent).toContain('@https://gw.example');
    expect(els['my-address-short'].title).toBe('did:key:z6MkExampleLongIdentifier@https://gw.example');
  });
  it('is a no-op with an empty address', () => {
    els['my-address-bar'] = mkEl({ style: { display: 'none' } });
    els['my-address-short'] = mkEl();
    const addr = make();
    addr.updateMyAddressBar('');
    expect(els['my-address-bar'].style.display).toBe('none');
  });
});

describe('copyMyAddress', () => {
  it('does nothing when no address is stored', () => {
    const addr = make();
    addr.copyMyAddress();
    expect(toasts).toHaveLength(0);
  });
  it('copies the stored address and toasts', async () => {
    store['proxion_my_address'] = 'did:key:zAbc@https://gw.example';
    els['copy-addr-btn'] = mkEl({ textContent: 'Copy' });
    const addr = make();
    addr.copyMyAddress();
    await Promise.resolve();
    expect(toasts).toContain('address.copied');
  });
});

describe('shareInviteLink', () => {
  it('warns when no invite link is available yet', () => {
    global.window = {};
    const addr = make();
    addr.shareInviteLink();
    expect(toasts[0]).toContain('address.noInviteLink');
  });
  it('opens the QR panel when an invite link exists', () => {
    global.window = { proxionInviteLink: 'https://gw.example/?join=ABC', proxionAddress: 'did:key:zAbc@https://gw.example' };
    global.QRCode = function () {}; global.QRCode.CorrectLevel = { M: 0 };
    els['qr-share-panel'] = mkEl({ style: { display: 'none' } });
    els['my-qr'] = mkEl();
    const addr = make();
    addr.shareInviteLink();
    expect(els['qr-share-panel'].style.display).toBe('block');
  });
});
