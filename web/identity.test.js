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
        getLocalDmPeers: () => over.localDmPeers ?? {},
        getPeerDeviceKeys: () => over.peerDeviceKeys ?? {},
        isCallCapablePeer: over.isCallCapablePeer ?? (() => false),
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

describe('multi-device fanout (R89)', () => {
    const dev = (id) => ({ device_id: id, pub_b64u: 'pub-' + id });

    it('devicesForPeer returns the peer account devices, or [] when unknown', () => {
        const r = make({ peerDeviceKeys: { [PEER]: [dev('d1'), dev('d2')] } });
        expect(r.devicesForPeer(PEER).map(d => d.device_id)).toEqual(['d1', 'd2']);
        expect(r.devicesForPeer('did:key:zNobody')).toEqual([]);
    });

    it('ownOtherDevices excludes THIS device (single-device: account == device did)', () => {
        // Single device: selfAccountDid == selfDeviceDid == DEV.
        const r = make({ clientDid: DEV, peerDeviceKeys: { [DEV]: [dev(DEV), dev('other')] } });
        expect(r.ownOtherDevices().map(d => d.device_id)).toEqual(['other']);
    });

    it('ownOtherDevices keys by the ACCOUNT and excludes this device (linked device)', () => {
        // Linked: account is ACCT, this device is DEV; roster is under the account.
        const r = make({ clientDid: DEV, accountDid: ACCT,
            peerDeviceKeys: { [ACCT]: [dev(DEV), dev('phone'), dev('laptop')] } });
        expect(r.ownOtherDevices().map(d => d.device_id)).toEqual(['phone', 'laptop']);
    });

    it('ownOtherDevices is empty when the roster is unknown or only this device', () => {
        expect(make({ clientDid: DEV }).ownOtherDevices()).toEqual([]);
        expect(make({ clientDid: DEV, peerDeviceKeys: { [DEV]: [dev(DEV)] } }).ownOtherDevices()).toEqual([]);
    });
});

describe('serverMuteKey (R87 mute-key reduction)', () => {
    it('returns the local DM peer webid for a local-DM thread', () => {
        const r = make({ localDmPeers: { t1: { peer_webid: PEER } } });
        expect(r.serverMuteKey('t1')).toBe(PEER);
    });
    it('reduces a cert-DM thread id to the peer did', () => {
        const r = make({ map: { [PEER]: 'cert-9' } });
        expect(r.serverMuteKey('cert-9')).toBe(PEER);
    });
    it('local DM peer wins over a cert-id match', () => {
        const r = make({ localDmPeers: { x: { peer_webid: PEER } }, map: { [ACCT]: 'x' } });
        expect(r.serverMuteKey('x')).toBe(PEER);
    });
    it('returns the thread id unchanged for a room (no match)', () => {
        expect(make().serverMuteKey('room-123')).toBe('room-123');
    });
    it('is safe for empty input', () => {
        expect(make().serverMuteKey('')).toBe('');
        expect(make().serverMuteKey(undefined)).toBe(undefined);
    });
});

describe('peerBindsCalls (R86 capability)', () => {
    it('is true only for a peer the capability source recognizes', () => {
        const capable = new Set([PEER]);
        const r = make({ isCallCapablePeer: (d) => capable.has(d) });
        expect(r.peerBindsCalls(PEER)).toBe(true);
        expect(r.peerBindsCalls(ACCT)).toBe(false);
    });
    it('is false for an empty/unknown contact', () => {
        const r = make({ isCallCapablePeer: () => true });
        expect(r.peerBindsCalls('')).toBe(false);
        expect(r.peerBindsCalls(null)).toBe(false);
    });
});
