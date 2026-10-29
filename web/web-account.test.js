// web-account.test.js: in the browser build the pod account is the identity, so
// one account's name, status and mutes must not carry over to the next account
// signed in on the same browser, while device preferences and invite links
// stashed across the OIDC redirect survive.
import { describe, it, expect, beforeEach } from 'vitest';
import {
    purgeWebAccount, adoptWebAccount, webIdHostLabel, shouldPromptForName, markNamePrompted,
    ACCOUNT_OWNER_KEY,
} from './web-account.js';

function memStorage(init = {}) {
    const s = { ...init };
    return {
        getItem: (k) => (k in s ? s[k] : null),
        setItem: (k, v) => { s[k] = String(v); },
        removeItem: (k) => { delete s[k]; },
        key: (i) => Object.keys(s)[i] ?? null,
        get length() { return Object.keys(s).length; },
        _dump: () => ({ ...s }),
    };
}

const ALICE = 'https://pod.example/alice/profile/card#me';
const BOB = 'https://pod.example/bob/profile/card#me';

describe('purgeWebAccount (web sign-out)', () => {
    it('drops the account name, status, pod session and stashed links; keeps device prefs', () => {
        const st = memStorage({
            proxion_display_name: 'Alice', proxion_status_message: 'busy',
            proxion_pending_join: 'r|x', proxion_pending_invite: 'addr',
            proxion_pod_webid: ALICE, proxion_pod_connected: '1',
            proxion_muted_threads: '["t"]', proxion_seen_room1: 'ts',
            [ACCOUNT_OWNER_KEY]: ALICE,
            proxion_theme: 'dark', proxion_locale: 'de', proxion_identity_did: 'did:key:z',
        });
        purgeWebAccount(st);
        expect(st._dump()).toEqual({ proxion_theme: 'dark', proxion_locale: 'de', proxion_identity_did: 'did:key:z' });
    });
});

describe('adoptWebAccount (web sign-in)', () => {
    let st;
    beforeEach(() => { st = memStorage(); });

    it("drops the previous account's name when a different WebID signs in", () => {
        st.setItem('proxion_display_name', 'Alice');
        st.setItem('proxion_status_message', 'at lunch');
        st.setItem(ACCOUNT_OWNER_KEY, ALICE);
        expect(adoptWebAccount(st, BOB)).toBe(true);
        expect(st.getItem('proxion_display_name')).toBe(null);
        expect(st.getItem('proxion_status_message')).toBe(null);
        expect(st.getItem(ACCOUNT_OWNER_KEY)).toBe(BOB);
    });

    it('keeps the name when the same account signs in again', () => {
        st.setItem('proxion_display_name', 'Alice');
        st.setItem(ACCOUNT_OWNER_KEY, ALICE);
        expect(adoptWebAccount(st, ALICE)).toBe(false);
        expect(st.getItem('proxion_display_name')).toBe('Alice');
    });

    it('trusts the last pod WebID for state written before the owner was tracked', () => {
        st.setItem('proxion_display_name', 'Alice');
        st.setItem('proxion_pod_webid', ALICE);
        expect(adoptWebAccount(st, ALICE)).toBe(false);
        expect(st.getItem('proxion_display_name')).toBe('Alice');
        expect(adoptWebAccount(memStorage({ proxion_display_name: 'Alice', proxion_pod_webid: ALICE }), BOB)).toBe(true);
    });

    it('drops a name of unknown owner (signed out without the purge)', () => {
        st.setItem('proxion_display_name', 'Alice');
        expect(adoptWebAccount(st, BOB)).toBe(true);
        expect(st.getItem('proxion_display_name')).toBe(null);
    });

    it('keeps an invite link stashed before the OIDC redirect', () => {
        st.setItem('proxion_display_name', 'Alice');
        st.setItem(ACCOUNT_OWNER_KEY, ALICE);
        st.setItem('proxion_pending_join', 'room|owner');
        adoptWebAccount(st, BOB);
        expect(st.getItem('proxion_pending_join')).toBe('room|owner');
    });
});

describe('name prompt', () => {
    it('asks once per WebID, and never when a name is set', () => {
        const st = memStorage();
        expect(shouldPromptForName(st, ALICE)).toBe(true);
        markNamePrompted(st, ALICE);
        expect(shouldPromptForName(st, ALICE)).toBe(false);
        expect(shouldPromptForName(st, BOB)).toBe(true);
        st.setItem('proxion_display_name', 'Bob');
        expect(shouldPromptForName(st, BOB)).toBe(false);
        expect(shouldPromptForName(st, '')).toBe(false);
    });

    it('the prompted marker survives a sign-out', () => {
        const st = memStorage();
        markNamePrompted(st, ALICE);
        purgeWebAccount(st);
        expect(shouldPromptForName(st, ALICE)).toBe(false);
    });

    it('labels a nameless account by its pod host', () => {
        expect(webIdHostLabel('http://localhost:54287/alice/profile/card#me')).toBe('localhost:54287');
        expect(webIdHostLabel('')).toBe('');
    });
});
