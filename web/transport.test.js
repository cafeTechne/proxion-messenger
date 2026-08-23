import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
    detectMode, createTransport, createGatewayTransport, createPodTransport,
    NotSupported, FEATURES, gatedControlIds, applyTransportGating,
} from './transport.js';

// The module reads window/document/localStorage defensively. Provide/withdraw
// them per test so we can exercise each detection branch.
let store;
function setStorage() {
    store = {};
    global.localStorage = {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
    };
}
afterEach(() => {
    delete global.window;
    delete global.document;
    delete global.localStorage;
    vi.restoreAllMocks();
});

describe('detectMode', () => {
    beforeEach(setStorage);

    it('defaults to gateway with no signals', () => {
        expect(detectMode()).toBe('gateway');
    });

    it('returns gateway under Tauri even if a web signal is present', () => {
        global.window = { __TAURI__: {}, location: { search: '?mode=web' } };
        expect(detectMode()).toBe('gateway');
    });

    it('honors the ?mode=web URL param and persists it', () => {
        global.window = { location: { search: '?mode=web' } };
        expect(detectMode()).toBe('web');
        expect(store.proxion_mode).toBe('web');
    });

    it('honors a <meta name="proxion-mode"> tag', () => {
        global.window = { location: { search: '' } };
        global.document = {
            querySelector: (sel) =>
                sel === 'meta[name="proxion-mode"]'
                    ? { getAttribute: () => 'web' }
                    : null,
        };
        expect(detectMode()).toBe('web');
    });

    it('falls back to a persisted localStorage mode', () => {
        store.proxion_mode = 'web';
        expect(detectMode()).toBe('web');
    });

    it('URL param outranks localStorage', () => {
        store.proxion_mode = 'web';
        global.window = { location: { search: '?mode=gateway' } };
        expect(detectMode()).toBe('gateway');
    });
});

describe('GatewayTransport', () => {
    it('supports every feature and routes sends through the connection', () => {
        const sent = [];
        const connection = {
            socketSendOrQueue: (p) => sent.push(p),
            connect: vi.fn(), flushPending: vi.fn(), forceReconnect: vi.fn(),
        };
        const t = createGatewayTransport({ connection });
        expect(t.mode).toBe('gateway');
        for (const f of FEATURES) expect(t.supports(f)).toBe(true);
        t.send({ cmd: 'x' });
        t.sendDM({ cmd: 'sealed_dm' });
        t.sendSignal({ cmd: 'ice_candidate' });
        t.publishPresence({ cmd: 'set_presence' });
        expect(sent).toEqual([
            { cmd: 'x' }, { cmd: 'sealed_dm' }, { cmd: 'ice_candidate' }, { cmd: 'set_presence' },
        ]);
        t.connect(); t.flushPending(); t.forceReconnect();
        expect(connection.connect).toHaveBeenCalled();
        expect(connection.flushPending).toHaveBeenCalled();
        expect(connection.forceReconnect).toHaveBeenCalled();
    });

    it('requires a connection', () => {
        expect(() => createGatewayTransport({})).toThrow();
    });
});

describe('PodTransport', () => {
    it('supports pod-backed rooms/history/invites and DMs (R102+R103)', () => {
        const t = createPodTransport();
        expect(t.mode).toBe('web');
        expect(t.supports('rooms')).toBe(true);
        expect(t.supports('history')).toBe(true);
        expect(t.supports('invites')).toBe(true);
        expect(t.supports('dm')).toBe(true);
        expect(t.supports('presence')).toBe(false);
        expect(t.supports('calls')).toBe(false);
    });

    it('throws NotSupported for realtime ops until later phases', () => {
        const t = createPodTransport();
        vi.spyOn(console, 'warn').mockImplementation(() => {});
        expect(() => t.sendDM({})).toThrow(NotSupported);
        expect(() => t.publishPresence({})).toThrow(NotSupported);
        expect(() => t.sendSignal({})).toThrow(NotSupported);
        expect(() => t.onIncomingDM(() => {})).toThrow(NotSupported);
    });

    it('connect/flushPending/forceReconnect are safe no-ops', () => {
        const t = createPodTransport();
        expect(() => { t.connect(); t.flushPending(); t.forceReconnect(); }).not.toThrow();
    });
});

describe('UI gating', () => {
    it('gates nothing in gateway mode', () => {
        const connection = { socketSendOrQueue: () => {} };
        expect(gatedControlIds(createGatewayTransport({ connection }))).toEqual([]);
    });

    it('gates call entry points but not DMs in the web build (R103)', () => {
        const ids = gatedControlIds(createPodTransport());
        expect(ids).not.toContain('add-peer-btn');   // DMs supported now
        expect(ids).toContain('start-call-btn');
        expect(ids).toContain('start-video-call-btn');
    });

    it('applyTransportGating hides the gated controls in a document', () => {
        const els = {
            'add-peer-btn': { style: {}, setAttribute(k, v) { this[k] = v; } },
            'start-call-btn': { style: {}, setAttribute(k, v) { this[k] = v; } },
            'start-video-call-btn': { style: {}, setAttribute(k, v) { this[k] = v; } },
        };
        const doc = { getElementById: (id) => els[id] || null };
        const hidden = applyTransportGating(createPodTransport(), doc);
        expect(hidden.sort()).toEqual(['start-call-btn', 'start-video-call-btn']);
        expect(els['start-call-btn'].style.display).toBe('none');
        expect(els['add-peer-btn'].style.display).toBeUndefined();   // DM entry stays visible
    });

    it('applyTransportGating is a no-op in gateway mode', () => {
        const connection = { socketSendOrQueue: () => {} };
        const doc = { getElementById: () => ({ style: {} }) };
        expect(applyTransportGating(createGatewayTransport({ connection }), doc)).toEqual([]);
    });
});

describe('createTransport', () => {
    beforeEach(setStorage);

    it('builds a PodTransport when mode is web', () => {
        expect(createTransport({ mode: 'web' }).mode).toBe('web');
    });

    it('builds a GatewayTransport when mode is gateway', () => {
        const connection = { socketSendOrQueue: () => {} };
        expect(createTransport({ mode: 'gateway', connection }).mode).toBe('gateway');
    });

    it('detects the mode when none is given', () => {
        expect(createTransport({ mode: 'web' }).mode).toBe('web');
        // No mode + no signals => gateway, which needs a connection.
        const connection = { socketSendOrQueue: () => {} };
        expect(createTransport({ connection }).mode).toBe('gateway');
    });
});
