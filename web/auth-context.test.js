import { describe, it, expect } from 'vitest';
import { createAuthContextTracker } from './auth-context.js';

describe('bound authentication generations', () => {
    it('keeps the account bound across expiry and resumes only after authentication', () => {
        const info = { isLoggedIn: true, webId: 'alice', sessionId: 'session' };
        const tracker = createAuthContextTracker(() => info), before = tracker.current();
        info.isLoggedIn = false;
        const expired = tracker.current();
        expect(expired.webId).toBe('alice'); expect(expired.authenticated).toBe(false);
        expect(expired.epoch).toBeGreaterThan(before.epoch);
        info.isLoggedIn = true;
        expect(tracker.current().epoch).toBeGreaterThan(expired.epoch);
    });
    it('cancels a generation on same-field restore events and rapid switch back', () => {
        const info = { isLoggedIn: true, webId: 'alice', sessionId: 'same' };
        const tracker = createAuthContextTracker(() => info), first = tracker.current();
        info.webId = 'bob'; tracker.current(); info.webId = 'alice';
        const returned = tracker.current(); expect(returned.epoch).toBeGreaterThan(first.epoch);
        tracker.note('sessionRestore'); expect(tracker.current().epoch).toBeGreaterThan(returned.epoch);
    });
    it('does not relatch stale SDK information immediately after explicit logout', () => {
        const info = { isLoggedIn: true, webId: 'alice', sessionId: 'same' };
        const tracker = createAuthContextTracker(() => info); tracker.current(); tracker.note('logout');
        expect(tracker.current().webId).toBeNull(); expect(tracker.current().authenticated).toBe(false);
        tracker.note('login'); expect(tracker.current().webId).toBe('alice');
    });
    it('honors expiry events even before the SDK flips its logged-in flag', () => {
        const info = { isLoggedIn: true, webId: 'alice' };
        const tracker = createAuthContextTracker(() => info); tracker.current(); tracker.note('sessionExpired');
        expect(tracker.current().webId).toBe('alice'); expect(tracker.current().authenticated).toBe(false);
        tracker.note('sessionExtended'); expect(tracker.current().authenticated).toBe(true);
    });
});
