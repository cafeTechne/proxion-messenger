import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createStatusBanners, applyBannerPriority, POD_BANNER_SNOOZE_KEY } from './status-banners.js';

let els, session, local, body, prepended;
function mkEl(over = {}) {
  return { style: {}, textContent: '', innerHTML: '', onclick: null, querySelector: () => ({}), ...over };
}
beforeEach(() => {
  els = {};
  session = {};
  local = {};
  prepended = [];
  body = { prepend: (n) => prepended.push(n) };
  global.document = {
    getElementById: (id) => (id in els ? els[id] : null),
    createElement: () => mkEl({ querySelector: () => ({ onclick: null }) }),
    body,
  };
  global.sessionStorage = {
    getItem: (k) => (k in session ? session[k] : null),
    setItem: (k, v) => { session[k] = String(v); },
  };
  global.localStorage = {
    getItem: (k) => (k in local ? local[k] : null),
    setItem: (k, v) => { local[k] = String(v); },
    removeItem: (k) => { delete local[k]; },
  };
});

function make() { return createStatusBanners(); }
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('_updateSettingsPodDot', () => {
  it('renders each pod state with its color + label', () => {
    els['settings-pod-status-dot'] = mkEl();
    const s = make();
    s._updateSettingsPodDot('connected');
    expect(els['settings-pod-status-dot'].textContent).toContain('connected');
    expect(els['settings-pod-status-dot'].style.color).toBe('var(--color-success-soft)');
    s._updateSettingsPodDot('unreachable');
    expect(els['settings-pod-status-dot'].textContent).toContain('unreachable');
    s._updateSettingsPodDot('none');
    expect(els['settings-pod-status-dot'].textContent).toContain('pod.dot.none');
  });
});

describe('setPodSyncIndicator / setPodBanner', () => {
  it('toggles the sync indicator', () => {
    els['pod-sync-indicator'] = mkEl({ style: { display: 'none' } });
    const s = make();
    s.setPodSyncIndicator(true);
    expect(els['pod-sync-indicator'].style.display).toBe('');
    s.setPodSyncIndicator(false);
    expect(els['pod-sync-indicator'].style.display).toBe('none');
  });
  it('toggles the connect banner with flex/none', () => {
    els['pod-connect-banner'] = mkEl({ style: { display: 'none' } });
    const s = make();
    s.setPodBanner(true);
    expect(els['pod-connect-banner'].style.display).toBe('flex');
    s.setPodBanner(false);
    expect(els['pod-connect-banner'].style.display).toBe('none');
  });
});

describe('setWebPodConnected (gateway-free session-driven pod state)', () => {
  const WEBID = 'https://alice.example/profile/card#me';
  function setupEls() {
    els['settings-pod-status-dot'] = mkEl();
    els['pod-connect-banner'] = mkEl({ style: { display: 'flex' } });
    els['settings-pod-connected'] = mkEl({ style: { display: 'none' } });
    els['settings-pod-disconnected'] = mkEl({ style: { display: 'block' } });
    els['settings-pod-webid'] = mkEl();
    els['username'] = mkEl({ textContent: 'Connecting...' });
  }

  it('marks the pod connected from the session: flag, dot, WebID, banner hidden', () => {
    setupEls();
    make().setWebPodConnected(true, WEBID);
    expect(local['proxion_pod_connected']).toBe('1');
    expect(local['proxion_pod_webid']).toBe(WEBID);
    expect(els['settings-pod-status-dot'].textContent).toContain('connected');
    expect(els['pod-connect-banner'].style.display).toBe('none');
    expect(els['settings-pod-connected'].style.display).toBe('block');
    expect(els['settings-pod-disconnected'].style.display).toBe('none');
    expect(els['settings-pod-webid'].textContent).toBe(WEBID);
  });

  it('clears the gateway "Connecting…" username, using the display name when set', () => {
    setupEls();
    local['proxion_display_name'] = 'Alice';
    make().setWebPodConnected(true, WEBID);
    expect(els['username'].textContent).toBe('Alice');
  });

  it('falls back to the WebID host when no display name is set', () => {
    setupEls();
    make().setWebPodConnected(true, WEBID);
    expect(els['username'].textContent).toBe('alice.example');
  });

  it('marks the pod disconnected when logged out: dot none, flag cleared, panel prompt', () => {
    setupEls();
    local['proxion_pod_connected'] = '1';
    make().setWebPodConnected(false);
    expect('proxion_pod_connected' in local).toBe(false);
    expect(els['settings-pod-status-dot'].textContent).toContain('pod.dot.none');
    expect(els['settings-pod-connected'].style.display).toBe('none');
    expect(els['settings-pod-disconnected'].style.display).toBe('block');
    expect(els['pod-connect-banner'].style.display).toBe('none');
    // The gateway placeholder is replaced rather than left as "Connecting…".
    expect(els['username'].textContent).not.toContain('Connecting');
  });
});

describe('_showNatWarning', () => {
  it('does nothing when already dismissed this session', async () => {
    session['proxion_nat_dismissed'] = '1';
    global.fetch = vi.fn();
    make()._showNatWarning();
    expect(global.fetch).not.toHaveBeenCalled();
  });
  it('does nothing when a banner already exists', () => {
    els['nat-warning-banner'] = mkEl();
    global.fetch = vi.fn();
    make()._showNatWarning();
    expect(global.fetch).not.toHaveBeenCalled();
  });
  it('skips the banner when the gateway is publicly reachable', async () => {
    global.fetch = vi.fn(async () => ({ json: async () => ({ public_url_set: true }) }));
    make()._showNatWarning();
    await flush();
    expect(prepended).toHaveLength(0);
  });
  it('prepends a guidance banner when not reachable', async () => {
    global.fetch = vi.fn(async () => ({ json: async () => ({ public_url_set: false, relay_fallback_active: false, upnp_mapped: false, local_port: 9000 }) }));
    make()._showNatWarning();
    await flush();
    expect(prepended).toHaveLength(1);
    expect(prepended[0].innerHTML).toContain('9000');
  });
  it('builds the banner on the shared warning class, not inline colors', async () => {
    global.fetch = vi.fn(async () => ({ json: async () => ({ public_url_set: false, relay_fallback_active: false }) }));
    make()._showNatWarning();
    await flush();
    expect(prepended[0].className).toBe('banner banner--warning');
    expect(prepended[0].style.cssText || '').not.toContain('#78350f');
  });
});

describe('banner slot priority', () => {
  // A banner element with a real-ish classList so the slot can mark it.
  function mkBanner({ display, visible = false } = {}) {
    const cls = new Set(visible ? ['banner', 'visible'] : ['banner']);
    return {
      style: display ? { display } : {},
      classList: {
        contains: (c) => cls.has(c),
        add: (c) => cls.add(c),
        remove: (c) => cls.delete(c),
        toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)),
      },
      _cls: cls,
    };
  }
  const suppressed = (el) => el._cls.has('banner--suppressed');

  it('shows only the highest-priority active banner', () => {
    els['conn-banner'] = mkBanner({ display: 'flex' });
    els['backup-nudge'] = mkBanner({ visible: true });
    els['pod-connect-banner'] = mkBanner({ display: 'flex' });
    expect(applyBannerPriority(global.document)).toBe('conn-banner');
    expect(suppressed(els['conn-banner'])).toBe(false);
    expect(suppressed(els['backup-nudge'])).toBe(true);
    expect(suppressed(els['pod-connect-banner'])).toBe(true);
  });

  it('follows connection > join pending > NAT > backup > pod', () => {
    els['conn-banner'] = mkBanner({ display: 'none' });
    els['join-pending-banner'] = mkBanner();
    els['nat-warning-banner'] = mkBanner();
    els['backup-nudge'] = mkBanner({ visible: true });
    els['pod-connect-banner'] = mkBanner({ display: 'flex' });
    expect(applyBannerPriority(global.document)).toBe('nat-warning-banner');
    delete els['nat-warning-banner'];
    expect(applyBannerPriority(global.document)).toBe('backup-nudge');
    els['join-pending-banner']._cls.add('visible');
    expect(applyBannerPriority(global.document)).toBe('join-pending-banner');
    expect(suppressed(els['backup-nudge'])).toBe(true);
  });

  it('un-suppresses a banner once the one above it goes away', () => {
    els['conn-banner'] = mkBanner({ display: 'flex' });
    els['pod-connect-banner'] = mkBanner({ display: 'flex' });
    applyBannerPriority(global.document);
    expect(suppressed(els['pod-connect-banner'])).toBe(true);
    els['conn-banner'].style.display = 'none';
    expect(applyBannerPriority(global.document)).toBe('pod-connect-banner');
    expect(suppressed(els['pod-connect-banner'])).toBe(false);
  });

  it('returns null when nothing is active', () => {
    els['conn-banner'] = mkBanner({ display: 'none' });
    expect(applyBannerPriority(global.document)).toBe(null);
  });
});

describe('pod banner snooze', () => {
  it('stays hidden while snoozed after skipping pod setup', () => {
    els['pod-connect-banner'] = mkEl({ style: { display: 'none' } });
    local[POD_BANNER_SNOOZE_KEY] = String(Date.now() + 60_000);
    make().setPodBanner(true);
    expect(els['pod-connect-banner'].style.display).toBe('none');
  });
  it('shows again once the snooze has passed', () => {
    els['pod-connect-banner'] = mkEl({ style: { display: 'none' } });
    local[POD_BANNER_SNOOZE_KEY] = String(Date.now() - 1);
    make().setPodBanner(true);
    expect(els['pod-connect-banner'].style.display).toBe('flex');
  });
});
