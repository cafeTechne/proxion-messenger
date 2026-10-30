// podhealth.test.js: the browser build notices a lapsed pod session, an
// unreachable pod and a lost network, and says when pod calls work again.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPodHealth } from './podhealth.js';

const POD = 'https://pod.example/alice/';

function mkSession(respond) {
    const handlers = {};
    return {
        info: { isLoggedIn: true, webId: 'https://pod.example/alice/profile/card#me' },
        events: {
            on: (name, fn) => { (handlers[name] = handlers[name] || []).push(fn); },
            emit: (name, ...a) => (handlers[name] || []).forEach((fn) => fn(...a)),
        },
        fetch: vi.fn(async (url) => respond(url)),
    };
}

function mkWin() {
    const h = {};
    return {
        navigator: { onLine: true },
        addEventListener: (n, fn) => { (h[n] = h[n] || []).push(fn); },
        fire: (n) => (h[n] || []).forEach((fn) => fn()),
        document: { visibilityState: 'visible', addEventListener: () => {} },
    };
}

let status;
let session;
let win;
let changes;
let recovered;
let health;

function build() {
    session = mkSession(async (url) => {
        if (status === 'neterr') throw new TypeError('Failed to fetch');
        return { ok: status < 400, status };
    });
    win = mkWin();
    changes = [];
    recovered = 0;
    health = createPodHealth({
        session, win,
        ownOrigins: () => [POD],
        probeUrl: () => POD,
        onChange: (s) => changes.push(s),
        onRecovered: () => { recovered++; },
    });
    health.start();
    health.markSignedIn();
}

beforeEach(() => { vi.useFakeTimers(); status = 200; build(); });
afterEach(() => { vi.useRealTimers(); });

describe('createPodHealth', () => {
    it('reports expired when the session ends (failed token refresh)', () => {
        session.events.emit('sessionExpired');
        expect(health.state()).toBe('expired');
        expect(changes).toEqual(['expired']);
    });

    it('ignores a sign-in error before the session was ever up', () => {
        build();
        const fresh = createPodHealth({ session, win, onChange: (s) => changes.push(s) });
        fresh.start();
        session.events.emit('error', 'access_denied');
        expect(fresh.state()).toBe('ok');
    });

    it('treats repeated 401s from our own pod as an expired session', async () => {
        status = 401;
        await session.fetch(POD + 'a');
        expect(health.state()).toBe('ok');            // one 401 is not enough
        await session.fetch(POD + 'b');
        expect(health.state()).toBe('expired');
    });

    it('does not count a 401 from someone else\'s server', async () => {
        status = 401;
        await session.fetch('https://other.example/x');
        await session.fetch('https://other.example/y');
        expect(health.state()).toBe('ok');
    });

    it('a working pod after 401s clears the state and reports recovery', async () => {
        status = 401;
        await session.fetch(POD + 'a');
        await session.fetch(POD + 'b');
        status = 200;
        await session.fetch(POD + 'c');
        expect(health.state()).toBe('ok');
        expect(recovered).toBe(1);
    });

    it('a session that ended stays expired until signed in again', async () => {
        session.events.emit('sessionExpired');
        session.info.isLoggedIn = false;
        await session.fetch(POD + 'public');           // anonymous read works
        expect(health.state()).toBe('expired');
        session.info.isLoggedIn = true;
        health.markSignedIn();
        expect(health.state()).toBe('ok');
        expect(recovered).toBe(1);
    });

    it('network errors make the pod unreachable, and it is probed again with backoff', async () => {
        status = 'neterr';
        await expect(session.fetch(POD + 'a')).rejects.toThrow();
        await expect(session.fetch(POD + 'b')).rejects.toThrow();
        expect(health.state()).toBe('unreachable');
        status = 200;
        await vi.advanceTimersByTimeAsync(5000);
        expect(health.state()).toBe('ok');
        expect(recovered).toBe(1);
    });

    it('a single failed write followed by a success still reports recovery', async () => {
        status = 503;
        await session.fetch(POD + 'w');
        expect(health.state()).toBe('ok');
        status = 200;
        await session.fetch(POD + 'poll');
        expect(recovered).toBe(1);
    });

    it('follows the browser offline and online events', async () => {
        win.fire('offline');
        expect(health.state()).toBe('offline');
        win.fire('online');
        expect(health.state()).toBe('ok');
        expect(recovered).toBe(1);
        expect(changes).toEqual(['offline', 'ok']);
    });
});
