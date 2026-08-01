// inboxacl.test.js — the inbox ACL builder (R75 base + R78 gateway reader). Pure.
import { describe, it, expect } from 'vitest';
import { buildInboxAcl } from './pod.js';

const INBOX = 'https://alice.pod/inbox/';
const OWNER = 'https://alice.pod/profile/card#me';
const GW = 'https://gw.pod/profile/card#me';

describe('buildInboxAcl', () => {
    it('always grants the owner control and the public Append (LDN norm)', () => {
        const ttl = buildInboxAcl(INBOX, OWNER, []);
        expect(ttl).toContain(`acl:agent <${OWNER}>`);
        expect(ttl).toContain('acl:Read, acl:Write, acl:Control');
        expect(ttl).toContain('acl:agentClass foaf:Agent');
        expect(ttl).toContain('acl:mode acl:Append');
        expect(ttl).not.toContain('#gatewayread');
    });

    it('adds a read-only authorization for a granted reader', () => {
        const ttl = buildInboxAcl(INBOX, OWNER, [GW]);
        expect(ttl).toContain('#gatewayread');
        expect(ttl).toContain(`acl:agent <${GW}>`);
        // The reader gets Read only — never Write/Control/Append.
        const block = ttl.split('#gatewayread')[1];
        expect(block).toContain('acl:mode acl:Read');
        expect(block).not.toContain('acl:Write');
        expect(block).not.toContain('acl:Append');
    });

    it('never grants the owner as a separate reader (dedup) and ignores blanks', () => {
        const ttl = buildInboxAcl(INBOX, OWNER, [OWNER, '', null]);
        expect(ttl).not.toContain('#gatewayread');
    });
});
