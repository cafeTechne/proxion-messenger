// ldn.test.js — the pure LDN protocol (build + parse). No I/O.
import { describe, it, expect } from 'vitest';
import {
    buildInviteNotification, parseInboxListing, parseInviteNotification,
    inviteActorVerified, AS, CONTAINS_PRED,
} from './ldn.js';

describe('buildInviteNotification', () => {
    it('produces an AS2 Invite naming actor, target, and the chat container', () => {
        const n = buildInviteNotification({
            from: 'https://alice.pod/profile/card#me',
            to: 'https://bob.pod/profile/card#me',
            container: 'https://alice.pod/proxion/rooms/team/',
            title: 'Team', published: '2026-07-31T00:00:00Z',
        });
        expect(n['@context']).toBe('https://www.w3.org/ns/activitystreams');
        expect(n.type).toBe('Invite');
        expect(n.actor).toBe('https://alice.pod/profile/card#me');
        expect(n.target).toBe('https://bob.pod/profile/card#me');
        expect(n.object).toMatchObject({ type: 'Link', href: 'https://alice.pod/proxion/rooms/team/', name: 'Team' });
        expect(n.published).toBe('2026-07-31T00:00:00Z');
    });

    it('a built invite round-trips through the parser', () => {
        const n = buildInviteNotification({
            from: 'https://a.pod/#me', to: 'https://b.pod/#me',
            container: 'https://a.pod/x/', title: 'Hi',
        });
        expect(parseInviteNotification(n)).toEqual({
            from: 'https://a.pod/#me', container: 'https://a.pod/x/', title: 'Hi', verified: true,
        });
    });
});

describe('parseInboxListing', () => {
    it('reads ldp:contains (expanded) and resolves relative refs', () => {
        const json = { '@id': 'https://b.pod/inbox/', [CONTAINS_PRED]: [{ '@id': 'n1' }, { '@id': 'https://b.pod/inbox/n2' }] };
        expect(parseInboxListing(json, 'https://b.pod/inbox/'))
            .toEqual(['https://b.pod/inbox/n1', 'https://b.pod/inbox/n2']);
    });

    it('reads a compacted `contains` term and de-dups', () => {
        const json = { contains: ['https://b.pod/inbox/n1', 'https://b.pod/inbox/n1'] };
        expect(parseInboxListing(json, 'https://b.pod/inbox/')).toEqual(['https://b.pod/inbox/n1']);
    });

    it('returns [] for an empty inbox', () => {
        expect(parseInboxListing({ '@id': 'https://b.pod/inbox/' }, 'https://b.pod/inbox/')).toEqual([]);
    });
});

describe('parseInviteNotification', () => {
    it('parses an expanded notification with a nested object Link', () => {
        const json = {
            [AS + 'actor']: [{ '@id': 'https://a.pod/#me' }],
            [AS + 'object']: [{ [AS + 'href']: [{ '@id': 'https://a.pod/x/' }], [AS + 'name']: [{ '@value': 'Room' }] }],
        };
        expect(parseInviteNotification(json)).toEqual({ from: 'https://a.pod/#me', container: 'https://a.pod/x/', title: 'Room', verified: true });
    });

    it('parses object as a bare container IRI, title from top-level name', () => {
        const json = { actor: 'https://a.pod/#me', object: 'https://a.pod/x/', name: 'Top' };
        expect(parseInviteNotification(json)).toEqual({ from: 'https://a.pod/#me', container: 'https://a.pod/x/', title: 'Top', verified: true });
    });

    it('ignores a notification that references no container (not a chat invite)', () => {
        expect(parseInviteNotification({ actor: 'https://a.pod/#me', object: 'https://a.pod/note' })).toBeNull();
    });

    it('tolerates a missing actor (returns from: "")', () => {
        expect(parseInviteNotification({ object: { href: 'https://a.pod/x/', name: 'X' } }))
            .toEqual({ from: '', container: 'https://a.pod/x/', title: 'X', verified: false });
    });

    it('flags an invite whose actor origin differs from the container as unverified', () => {
        // The inbox is public-Append: an attacker names a trusted WebID as actor
        // but points the container at their own pod. Origins differ -> unverified.
        const json = { actor: 'https://alice.pod/profile/card#me', object: 'https://mallory.pod/trap/' };
        expect(parseInviteNotification(json)).toEqual({
            from: 'https://alice.pod/profile/card#me', container: 'https://mallory.pod/trap/', title: '', verified: false,
        });
    });

    it('does not treat a title as an IRI, nor an IRI as a title', () => {
        const n = parseInviteNotification({ actor: 'https://a.pod/#me', object: { href: 'https://a.pod/x/', name: 'not-a-url' } });
        expect(n.container).toBe('https://a.pod/x/');
        expect(n.title).toBe('not-a-url');
    });
});

describe('inviteActorVerified', () => {
    it('trusts an actor whose WebID shares the container origin', () => {
        expect(inviteActorVerified('https://alice.pod/alice/profile/card#me', 'https://alice.pod/x/')).toBe(true);
    });
    it('rejects a cross-origin container (spoof)', () => {
        expect(inviteActorVerified('https://alice.pod/profile/card#me', 'https://mallory.pod/trap/')).toBe(false);
    });
    it('rejects a missing or malformed actor/container', () => {
        expect(inviteActorVerified('', 'https://a.pod/x/')).toBe(false);
        expect(inviteActorVerified('https://a.pod/#me', 'not-a-url')).toBe(false);
    });
});
