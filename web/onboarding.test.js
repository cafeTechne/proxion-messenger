import { describe, it, expect, vi, beforeEach } from 'vitest';
// Stub the browser OIDC entry point so obPodSignIn can be exercised without the
// real solid-authn bundle trying to redirect.
vi.mock('./auth.js', () => ({ solidLogin: vi.fn(() => Promise.resolve()) }));
import { solidLogin } from './auth.js';
import { createOnboarding, obProgress, OB_STEPS, obReopenStep, OB_AFTER_SKIP_KEY } from './onboarding.js';

// Flexible element stub: every getElementById returns a fresh fake element that
// records whatever the wizard sets on it.
let els;
function mkEl(over = {}) {
  return {
    value: '', textContent: '', innerText: '', checked: false,
    disabled: false, style: {}, focus() {}, click() {}, ...over,
  };
}
beforeEach(() => {
  els = {};
  global.document = {
    getElementById: (id) => (els[id] ||= mkEl()),
    querySelector: () => null,
  };
  const store = {};
  global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  global.window = { confirm: () => true };
  global.WebSocket = { OPEN: 1 };
});

function make(over = {}) {
  const sent = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const setPodBanner = vi.fn();
  const ob = createOnboarding({
    getSocket: () => over.socket === undefined ? socket : over.socket,
    setPodBanner,
    showToast: () => {},
    showCopyModal: () => {},
  });
  return { ob, sent, setPodBanner };
}

describe('obReopenStep', () => {
  const set = (o) => Object.entries(o).forEach(([k, v]) => localStorage.setItem(k, v));
  it('starts a brand-new user at step 1 and a named one at the pod step', () => {
    expect(obReopenStep(localStorage)).toBe(1);
    set({ proxion_display_name: 'Ann' });
    expect(obReopenStep(localStorage)).toBe(4);
  });
  it('never reopens for a finished wizard or a linked device', () => {
    set({ proxion_display_name: 'Ann', proxion_wizard_done: '1' });
    expect(obReopenStep(localStorage)).toBe(null);
    localStorage.removeItem('proxion_wizard_done');
    localStorage.removeItem('proxion_display_name');
    set({ proxion_delegation_cert: '{}' });
    expect(obReopenStep(localStorage)).toBe(null);
  });
  it('returns a user who skipped the pod to the step they left', () => {
    const { ob } = make();
    set({ proxion_display_name: 'Ann' });
    ob.obSkipPod();
    expect(obReopenStep(localStorage)).toBe(5);
    ob.obGoto(6);
    expect(obReopenStep(localStorage)).toBe(6);
    ob.finishOnboarding();
    expect(localStorage.getItem(OB_AFTER_SKIP_KEY)).toBe(null);
    expect(obReopenStep(localStorage)).toBe(null);
  });
  it('leaves an older install that skipped the pod alone', () => {
    set({ proxion_display_name: 'Ann', proxion_pod_setup_skipped: '1' });
    expect(obReopenStep(localStorage)).toBe(null);
  });
});

describe('obGoto', () => {
  it('shows the target step and hides the others', () => {
    const { ob } = make();
    ob.obGoto(2);
    expect(els['ob-step-2'].style.display).toBe('block');
    expect(els['ob-step-1'].style.display).toBe('none');
    expect(els['ob-step-4'].style.display).toBe('none');
  });
});

describe('obBack', () => {
  it('returns to the step the user came from, and hides Back on the first', () => {
    const { ob } = make();
    els['ob-back-btn'] = mkEl({ hidden: true });
    ob.obGoto(1);
    expect(els['ob-back-btn'].hidden).toBe(true);
    ob.obGoto(2);
    expect(els['ob-back-btn'].hidden).toBe(false);
    ob.obGoto(4);
    ob.obBack();
    expect(els['ob-step-2'].style.display).toBe('block');
    ob.obBack();
    expect(els['ob-step-1'].style.display).toBe('block');
    expect(els['ob-back-btn'].hidden).toBe(true);
  });
  it('offers no Back to a returning user dropped onto a step', () => {
    const { ob } = make();
    els['ob-back-btn'] = mkEl({ hidden: true });
    ob.obGoto(2);
    ob.obGoto(4, { returning: true });
    expect(els['ob-back-btn'].hidden).toBe(true);
  });
  it('has no Back on the last step', () => {
    const { ob } = make();
    els['ob-back-btn'] = mkEl({ hidden: true });
    ob.obGoto(1);
    ob.obGoto(5);
    ob.obGoto(6);
    expect(els['ob-back-btn'].hidden).toBe(true);
  });
});

describe('wizard progress', () => {
  it('counts only the visible steps (1, 2, 4, 5, 6)', () => {
    expect(OB_STEPS).toEqual([1, 2, 4, 5, 6]);
    expect(obProgress(1)).toEqual({ n: 1, total: 5 });
    expect(obProgress(4)).toEqual({ n: 3, total: 5 });
    expect(obProgress(6)).toEqual({ n: 5, total: 5 });
    expect(obProgress(3)).toBeNull();
  });
  it('obGoto writes the progress label', () => {
    const { ob } = make();
    ob.obGoto(5);
    expect(els['ob-progress'].textContent).toBe('onboarding.progress');
  });
  it('obGoto(6) shows a localized placeholder until the address is known', () => {
    const { ob } = make();
    ob.obGoto(6);
    expect(els['ob-my-addr'].textContent).toBe('onboarding.addressPending');
  });
  it('obGoto(6) asks the gateway for the address when none is known yet', () => {
    const { ob, sent } = make();
    ob.obGoto(6);
    expect(sent).toContainEqual({ cmd: 'get_my_address' });
  });
  it('obGoto(6) does not ask again once the address is known', () => {
    const { ob, sent } = make();
    localStorage.setItem('proxion_my_address', 'alice@example.org');
    ob.obGoto(6);
    expect(els['ob-my-addr'].textContent).toBe('alice@example.org');
    expect(sent).toEqual([]);
  });
});

describe('obStep2', () => {
  it('rejects an empty name without sending', () => {
    const { ob, sent } = make();
    els['ob-name'] = mkEl({ value: '   ' });
    ob.obStep2();
    expect(sent).toHaveLength(0);
    expect(els['ob-name-error'].textContent).toBe('onboarding.nameRequired');
  });
  it('clears the name error once a name is given', () => {
    const { ob } = make();
    els['ob-name'] = mkEl({ value: '' });
    ob.obStep2();
    els['ob-name'].value = 'Alice';
    ob.obStep2();
    expect(els['ob-name-error'].textContent).toBe('');
  });
  it('persists the name and sends set_identity', () => {
    const { ob, sent } = make();
    els['ob-name'] = mkEl({ value: 'Alice' });
    ob.obStep2();
    expect(localStorage.getItem('proxion_display_name')).toBe('Alice');
    expect(sent).toContainEqual({ cmd: 'set_identity', display_name: 'Alice' });
  });
});

describe('obStep4Join', () => {
  it('joins a link to another gateway through this gateway', () => {
    const { ob, sent } = make();
    els['ob-invite-code'] = mkEl({ value: 'https://example.com/?join=ABC123' });
    ob.obStep4Join();
    expect(sent).toContainEqual({ cmd: 'join_remote_room', url: 'https://example.com/?join=ABC123' });
  });
  it('extracts the join code from an invite URL for this gateway', () => {
    window.location = { origin: 'https://example.com' };
    const { ob, sent } = make();
    els['ob-invite-code'] = mkEl({ value: 'https://example.com/?join=ABC123' });
    ob.obStep4Join();
    expect(sent).toContainEqual({ cmd: 'join_room', code: 'ABC123' });
  });
  it('uses a bare code as-is', () => {
    const { ob, sent } = make();
    els['ob-invite-code'] = mkEl({ value: 'PLAINCODE' });
    ob.obStep4Join();
    expect(sent).toContainEqual({ cmd: 'join_room', code: 'PLAINCODE' });
  });
  it('explains an empty invite field instead of doing nothing', () => {
    const { ob, sent } = make();
    let focused = false;
    els['ob-invite-code'] = mkEl({ value: '  ', focus() { focused = true; } });
    ob.obStep4Join();
    expect(sent).toHaveLength(0);
    expect(els['ob-join-error'].textContent).toBe('onboarding.inviteRequired');
    expect(focused).toBe(true);
  });
  it('is a no-op with no socket', () => {
    const { ob, sent } = make({ socket: null });
    els['ob-invite-code'] = mkEl({ value: 'PLAINCODE' });
    ob.obStep4Join();
    expect(sent).toHaveLength(0);
  });
});

describe('obPodSignIn (Phase E: browser sign-in after pod creation)', () => {
  beforeEach(() => solidLogin.mockClear());

  it('sets the wizard resume flag and starts OIDC login to the chosen provider', () => {
    const { ob } = make();
    ob.obSelectProvider('https://solidcommunity.net');
    ob.obPodSignIn();
    // The full-page OIDC redirect drops us back at boot, so the wizard is told to
    // resume at the room step (5) when the app reloads logged in.
    expect(localStorage.getItem('proxion_ob_resume')).toBe('5');
    expect(solidLogin).toHaveBeenCalledWith('https://solidcommunity.net');
  });

  it('uses the custom CSS URL (trailing slash stripped) for "my own server"', () => {
    const { ob } = make();
    ob.obSelectProvider('custom');
    els['ob-pod-css-url'].value = 'http://localhost:3001/';
    ob.obPodSignIn();
    expect(solidLogin).toHaveBeenCalledWith('http://localhost:3001');
    expect(localStorage.getItem('proxion_ob_resume')).toBe('5');
  });

  it('does nothing when no pod URL is available', () => {
    const { ob } = make();
    ob.obSelectProvider('custom');           // custom picked but input left empty
    ob.obPodSignIn();
    expect(solidLogin).not.toHaveBeenCalled();
    expect(localStorage.getItem('proxion_ob_resume')).toBe(null);
  });
});

describe('obSkipPod', () => {
  it('does nothing when the user cancels the confirm', () => {
    const { ob, setPodBanner } = make();
    global.window.confirm = () => false;
    ob.obSkipPod();
    expect(setPodBanner).not.toHaveBeenCalled();
    expect(localStorage.getItem('proxion_pod_setup_skipped')).toBe(null);
  });
  it('marks pod setup skipped and shows the banner when confirmed', () => {
    const { ob, setPodBanner } = make();
    global.window.confirm = () => true;
    ob.obSkipPod();
    expect(localStorage.getItem('proxion_pod_setup_skipped')).toBe('1');
    expect(setPodBanner).toHaveBeenCalledWith(true);
    // The banner is snoozed for a week so it does not pop up right away.
    const until = Number(localStorage.getItem('proxion_pod_banner_snooze_until'));
    expect(until - Date.now()).toBeGreaterThan(6 * 24 * 60 * 60 * 1000);
  });
});

describe('obPodTestConnection', () => {
  function fill(ob) {
    ob.obSelectProvider('custom');
    els['ob-pod-css-url'].value = 'https://pod.example';
    els['ob-pod-email'].value = 'a@b.c';
    els['ob-pod-password'].value = 'pw';
  }
  it('posts same-origin when the page is served by the gateway', async () => {
    // First run: my_address has not arrived yet, so no stored gateway URL.
    window.location = { protocol: 'https:' };
    global.fetch = vi.fn(async () => ({ json: async () => ({ status: 'ok' }) }));
    const { ob } = make();
    fill(ob);
    await ob.obPodTestConnection();
    expect(global.fetch.mock.calls[0][0]).toBe('/setup/pod');
    expect(els['ob-pod-continue-btn'].disabled).toBe(false);
  });
  it('posts to the loopback sidecar from the desktop shell', async () => {
    window.location = { protocol: 'tauri:', hostname: 'localhost' };
    // The advertised gateway URL can be a public tunnel; the shell's own
    // gateway is always the loopback sidecar.
    localStorage.setItem('proxion_gateway_http_url', 'https://calm-x.trycloudflare.com/');
    global.fetch = vi.fn(async () => ({ json: async () => ({ status: 'ok' }) }));
    const { ob } = make();
    fill(ob);
    await ob.obPodTestConnection();
    expect(global.fetch.mock.calls[0][0]).toBe('http://127.0.0.1:8080/setup/pod');
  });
});
