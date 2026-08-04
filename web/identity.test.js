import { describe, it, expect } from 'vitest';
import { createIdentityResolver } from './identity.js';

const ACCT = 'did:key:zAccount';
const DEV = 'did:key:zDevice';
const PEER = 'did:key:zPeer';
const GATEWAY = 'did:key:zGateway';

function make(over = {}) {
    return createIdentityResolver({
        getClientDid: () => over.clientDid ?? DEV,
        getAccountDid: () => over.accountDid ?? null,
        getPeerDidToCertId: () => over.map ?? {},
    });
}

describe('self identity', () => {
    it('selfDeviceDid is always the signing key', () => {
        expect(make().selfDeviceDid()).toBe(DEV);
    });
    it('selfAccountDid is the account when linked, else the device key', () => {
        expect(make({ accountDid: ACCT }).selfAccountDid()).toBe(ACCT);
        expect(make().selfAccountDid()).toBe(DEV);   // single device: device IS the account
    });
});

describe('contactForCall (reduce a call identity to a known contact)', () => {
    it('resolves an account did on the event when it is a known contact', () => {
        const r = make({ map: { [PEER]: 'cert-1' } });
        expect(r.contactForCall(null, { caller_webid: PEER })).toBe(PEER);
    });

    it('prefers caller_webid over from_webid', () => {
        const r = make({ map: { [PEER]: 'cert-1' } });
        expect(r.contactForCall(null, { caller_webid: PEER, from_webid: GATEWAY })).toBe(PEER);
    });

    it('does NOT resolve a gateway did that is not a known contact', () => {
        // The relayed event's from_webid is a gateway did with no relationship: not a
        // contact, so it must not be returned (that was the cross-gateway verify bug).
        const r = make({ map: { [PEER]: 'cert-1' } });
        expect(r.contactForCall(null, { from_webid: GATEWAY })).toBe('');
    });

    it('falls back to the open thread peer when the event has no known identity', () => {
        const r = make({ map: {} });
        expect(r.contactForCall({ peerWebid: PEER }, { from_webid: GATEWAY })).toBe(PEER);
    });

    it('resolves a thread by cert id back to its peer did', () => {
        const r = make({ map: { [PEER]: 'cert-1' } });
        expect(r.contactForCall({ id: 'cert-1' }, null)).toBe(PEER);
    });

    it('returns empty when nothing is recognized', () => {
        expect(make().contactForCall(null, { from_webid: GATEWAY })).toBe('');
        expect(make().contactForCall(null, null)).toBe('');
    });
});
