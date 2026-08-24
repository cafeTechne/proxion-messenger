import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// The hosted Solid-OIDC Client Identifier Document (R102.2). Its fields are
// deployment-coupled: client_id must equal the URL it is served from, and
// redirect_uris must include the app URL the web build redirects back to.
const doc = JSON.parse(
    readFileSync(fileURLToPath(new URL('./clientid.jsonld', import.meta.url)), 'utf8'),
);

describe('clientid.jsonld', () => {
    it('declares the Solid-OIDC context', () => {
        expect(doc['@context']).toContain('https://www.w3.org/ns/solid/oidc-context.jsonld');
    });

    it('client_id is the document URL and lives under client_uri', () => {
        expect(doc.client_id).toMatch(/\/clientid\.jsonld$/);
        expect(doc.client_id.startsWith(doc.client_uri)).toBe(true);
    });

    it('redirect_uris includes the app origin (client_uri)', () => {
        expect(Array.isArray(doc.redirect_uris)).toBe(true);
        expect(doc.redirect_uris).toContain(doc.client_uri);
    });

    it('is a public client using the authorization-code + refresh flow', () => {
        expect(doc.token_endpoint_auth_method).toBe('none');
        expect(doc.grant_types).toEqual(expect.arrayContaining(['authorization_code', 'refresh_token']));
        expect(doc.response_types).toContain('code');
    });

    it('requests the webid + offline_access scopes', () => {
        expect(doc.scope.split(/\s+/)).toEqual(expect.arrayContaining(['openid', 'webid', 'offline_access']));
    });
});
