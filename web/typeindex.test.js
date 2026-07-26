// typeindex.test.js — PLAN_ROUND_70 Track D: making chats discoverable via the
// Solid Type Index. Pure builders + parsers; the live round trip is in
// podcanonical-typeindex.test.js.
import { describe, it, expect } from 'vitest';
import {
    registrationId, buildEmptyTypeIndex, buildRegisterPatch, buildDeregisterPatch,
    buildProfileLinkPatch, parsePublicTypeIndex, parseRegisteredContainers,
    CHAT_CLASS, NS,
} from './typeindex.js';

const INDEX = 'https://me.pod/settings/publicTypeIndex.ttl';
const CONTAINER = 'https://me.pod/proxion/rooms/general/';
const WEBID = 'https://me.pod/profile/card#me';

describe('registrationId', () => {
    it('is deterministic per container (idempotent register/deregister)', () => {
        expect(registrationId(CONTAINER)).toBe(registrationId(CONTAINER));
    });
    it('differs across containers and is injection-free (hex only)', () => {
        expect(registrationId(CONTAINER)).not.toBe(registrationId('https://me.pod/proxion/rooms/other/'));
        expect(registrationId(CONTAINER)).toMatch(/^reg-[0-9a-f]+$/);
    });
});

describe('buildEmptyTypeIndex', () => {
    it('declares a TypeIndex ListedDocument', () => {
        const ttl = buildEmptyTypeIndex();
        expect(ttl).toContain('a solid:TypeIndex, solid:ListedDocument');
        expect(ttl).toContain(`@prefix solid: <${NS.solid}>`);
    });
});

describe('buildRegisterPatch / buildDeregisterPatch', () => {
    it('registers the container for meeting:LongChat with a stable node id', () => {
        const patch = buildRegisterPatch({ indexUrl: INDEX, containerUrl: CONTAINER });
        const reg = `${INDEX}#${registrationId(CONTAINER)}`;
        expect(patch).toContain('INSERT DATA {');
        expect(patch).toContain(`<${reg}> <${NS.rdf}type> <${NS.solid}TypeRegistration> .`);
        expect(patch).toContain(`<${reg}> <${NS.solid}forClass> <${CHAT_CLASS}> .`);
        expect(patch).toContain(`<${reg}> <${NS.solid}instanceContainer> <${CONTAINER}> .`);
    });

    it('deregister emits the SAME triples as DELETE DATA (exact match)', () => {
        const reg = buildRegisterPatch({ indexUrl: INDEX, containerUrl: CONTAINER })
            .replace('INSERT DATA', '').trim();
        const dereg = buildDeregisterPatch({ indexUrl: INDEX, containerUrl: CONTAINER })
            .replace('DELETE DATA', '').trim();
        expect(dereg).toBe(reg);
    });

    it('is injection-hardened: a hostile container cannot break out of the IRI', () => {
        const evil = 'https://me.pod/x/> <p> <q> .\n<s> <p> <o';
        const patch = buildRegisterPatch({ indexUrl: INDEX, containerUrl: evil });
        // iriRef strips the space/newline/angle chars, so no stray triple appears.
        expect(patch).not.toContain('> <p> <q>');
        expect(patch.match(/instanceContainer/g)).toHaveLength(1);
    });
});

describe('buildProfileLinkPatch', () => {
    it('links the WebID to its public type index', () => {
        const patch = buildProfileLinkPatch({ webId: WEBID, indexUrl: INDEX });
        expect(patch).toContain(`<${WEBID}> <${NS.solid}publicTypeIndex> <${INDEX}> .`);
    });
});

describe('parsePublicTypeIndex', () => {
    it('extracts the index IRI from a profile graph', () => {
        const json = { '@id': WEBID, [NS.solid + 'publicTypeIndex']: [{ '@id': INDEX }] };
        expect(parsePublicTypeIndex(json, WEBID)).toBe(INDEX);
    });
    it('returns null when the profile has no index', () => {
        expect(parsePublicTypeIndex({ '@id': WEBID }, WEBID)).toBe(null);
    });
});

describe('parseRegisteredContainers', () => {
    const index = () => [
        {
            '@id': `${INDEX}#reg-a`,
            [NS.solid + 'forClass']: [{ '@id': CHAT_CLASS }],
            [NS.solid + 'instanceContainer']: [{ '@id': CONTAINER }],
        },
        {
            '@id': `${INDEX}#reg-b`,
            [NS.solid + 'forClass']: [{ '@id': 'http://www.w3.org/2006/vcard/ns#AddressBook' }],
            [NS.solid + 'instanceContainer']: [{ '@id': 'https://me.pod/contacts/' }],
        },
    ];

    it('returns only the meeting:LongChat containers', () => {
        expect(parseRegisteredContainers(index())).toEqual([CONTAINER]);   // not the address book
    });

    it('dedups repeated container registrations', () => {
        const dup = index().concat(index());
        expect(parseRegisteredContainers({ '@graph': dup })).toEqual([CONTAINER]);
    });
});
