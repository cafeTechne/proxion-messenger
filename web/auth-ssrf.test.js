import { describe, it, expect, vi, beforeEach } from 'vitest';
import { isPrivatePodHost, isPeerPodRootAllowed } from './ssrf.js';

// Stub the browser auth bundle so auth.js imports with a controllable session in
// the node test env (its Session is otherwise the real solid-client-authn one).
vi.mock('./solid-authn.bundle.js', () => ({
    default: {
        Session: class {
            constructor() { this.info = { isLoggedIn: true, webId: null }; }
            async handleIncomingRedirect() {}
            async login() {}
            async logout() {}
            async fetch() { return { ok: false, status: 404 }; }
        },
    },
}));

import { solidSession, podStorageRoot, solidLogout } from './auth.js';

function memLocalStorage() {
    const s = {};
    return {
        getItem: (k) => (k in s ? s[k] : null),
        setItem: (k, v) => { s[k] = String(v); },
        removeItem: (k) => { delete s[k]; },
    };
}

describe('isPrivatePodHost', () => {
    it('flags loopback / private / link-local hosts', () => {
        for (const u of [
            'https://127.0.0.1/', 'http://localhost/', 'https://app.localhost/',
            'https://10.0.0.5/', 'https://192.168.1.9/', 'https://172.16.0.1/',
            'https://169.254.10.10/', 'https://0.0.0.0/',
            'https://[::1]/', 'https://[fe80::1]/', 'https://[fc00::1]/', 'https://[fd12::1]/',
            'https://[::ffff:127.0.0.1]/',
        ]) expect(isPrivatePodHost(u), u).toBe(true);
    });
    it('allows public hosts', () => {
        for (const u of [
            'https://pod.example.com/', 'https://storage.inrupt.com/abc/',
            'https://8.8.8.8/', 'https://172.15.0.1/', 'https://172.32.0.1/',
        ]) expect(isPrivatePodHost(u), u).toBe(false);
    });
    it('treats an unparseable URL as unsafe', () => {
        expect(isPrivatePodHost('not a url')).toBe(true);
    });
});

describe('isPeerPodRootAllowed', () => {
    it('allows any public https peer (cross-pod federation)', () => {
        expect(isPeerPodRootAllowed('https://bob.pod.example/', 'https://me.pod.example/')).toBe(true);
    });
    it('blocks a private peer when it is not our own pod origin', () => {
        expect(isPeerPodRootAllowed('https://127.0.0.1/', 'https://me.pod.example/')).toBe(false);
        expect(isPeerPodRootAllowed('https://[::1]:3000/', null)).toBe(false);
    });
    it('allows a private peer that shares our own pod origin (local/dev)', () => {
        expect(isPeerPodRootAllowed('http://localhost:3000/bob/', 'http://localhost:3000/me/')).toBe(true);
    });
    it('refuses an empty root', () => {
        expect(isPeerPodRootAllowed('', 'https://me.pod.example/')).toBe(false);
    });
});

describe('persisted storage root is bound per-WebID (no same-origin cache poisoning)', () => {
    const ALICE = 'https://shared.pod.example/alice/profile/card#me';
    const BOB = 'https://shared.pod.example/bob/profile/card#me';
    const ALICE_ROOT = 'https://shared.pod.example/alice/';

    beforeEach(async () => {
        globalThis.localStorage = memLocalStorage();
        await solidLogout();                 // resets the in-memory root cache + clears storage
        globalThis.localStorage = memLocalStorage();
    });

    it('does not return WebID A\'s cached root when signed in as WebID B on the same origin', () => {
        // Alice's root is cached; signing in as Bob (same origin, no logout) must not adopt it.
        localStorage.setItem('proxion_storage_root_v2', JSON.stringify({ webId: ALICE, root: ALICE_ROOT }));
        solidSession.info.webId = BOB;
        expect(podStorageRoot()).toBe('https://shared.pod.example/bob/');   // derived, not Alice's
        expect(localStorage.getItem('proxion_storage_root_v2')).toBe(null); // poisoned entry dropped
    });

    it('still returns a root cached for the same WebID', () => {
        localStorage.setItem('proxion_storage_root_v2', JSON.stringify({ webId: ALICE, root: ALICE_ROOT }));
        solidSession.info.webId = ALICE;
        expect(podStorageRoot()).toBe(ALICE_ROOT);
    });

    it('drops a legacy bare-string entry (no bound WebID)', () => {
        localStorage.setItem('proxion_storage_root_v2', ALICE_ROOT);
        solidSession.info.webId = ALICE;
        expect(podStorageRoot()).toBe(ALICE_ROOT);                         // re-derived, still correct
        expect(localStorage.getItem('proxion_storage_root_v2')).toBe(null); // untrusted legacy value dropped
    });
});
