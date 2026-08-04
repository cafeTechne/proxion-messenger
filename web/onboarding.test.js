import { describe, it, expect, vi, beforeEach } from 'vitest';
// Stub the browser OIDC entry point so obPodSignIn can be exercised without the
// real solid-authn bundle trying to redirect.
vi.mock('./auth.js', () => ({ solidLogin: vi.fn(() => Promise.resolve()) }));
import { solidLogin } from './auth.js';
import { createOnboarding } from './onboarding.js';

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

describe('obGoto', () => {
  it('shows the target step and hides the others', () => {
    const { ob } = make();
    ob.obGoto(2);
    expect(els['ob-step-2'].style.display).toBe('block');
    expect(els['ob-step-1'].style.display).toBe('none');
    expect(els['ob-step-4'].style.display).toBe('none');
  });
});

describe('obStep2', () => {
  it('rejects an empty name without sending', () => {
    const { ob, sent } = make();
    els['ob-name'] = mkEl({ value: '   ' });
    ob.obStep2();
    expect(sent).toHaveLength(0);
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
  it('extracts the join code from a full invite URL', () => {
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
  });
});
