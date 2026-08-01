// ldn.test.js — the pure LDN protocol (build + parse). No I/O.
import { describe, it, expect } from 'vitest';
import {
    buildInviteNotification, parseInboxListing, parseInviteNotification,
    AS, CONTAINS_PRED,
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
            from: 'https://a.pod/#me', container: 'https://a.pod/x/', title: 'Hi',
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
        expect(parseInviteNotification(json)).toEqual({ from: 'https://a.pod/#me', container: 'https://a.pod/x/', title: 'Room' });
    });

    it('parses object as a bare container IRI, title from top-level name', () => {
        const json = { actor: 'https://a.pod/#me', object: 'https://a.pod/x/', name: 'Top' };
        expect(parseInviteNotification(json)).toEqual({ from: 'https://a.pod/#me', container: 'https://a.pod/x/', title: 'Top' });
    });

    it('ignores a notification that references no container (not a chat invite)', () => {
        expect(parseInviteNotification({ actor: 'https://a.pod/#me', object: 'https://a.pod/note' })).toBeNull();
    });

    it('tolerates a missing actor (returns from: "")', () => {
        expect(parseInviteNotification({ object: { href: 'https://a.pod/x/', name: 'X' } }))
            .toEqual({ from: '', container: 'https://a.pod/x/', title: 'X' });
    });

    it('does not treat a title as an IRI, nor an IRI as a title', () => {
        const n = parseInviteNotification({ actor: 'https://a.pod/#me', object: { href: 'https://a.pod/x/', name: 'not-a-url' } });
        expect(n.container).toBe('https://a.pod/x/');
        expect(n.title).toBe('not-a-url');
    });
});
