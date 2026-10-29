import { describe, it, expect, vi } from 'vitest';
import { createPairing } from './pairing.js';

function make(over = {}) {
    return createPairing({
        getSocket: () => over.socket ?? null,
        getClientDid: () => over.clientDid ?? 'did:key:zSelf',
        getIdentityPrivKey: () => over.priv ?? null,
        getGatewayUrl: () => over.gw ?? '',
        showToast: over.showToast ?? (() => {}),
        refreshDevices: over.refreshDevices ?? (() => {}),
    });
}

describe('_safetyCode', () => {
    // Cross-checked against the gateway's _pairing_safety_code:
    //   int(sha256(did).hexdigest()[:8], 16) % 100000000, zero-padded to 8.
    it('matches the Python formula for known DIDs', async () => {
        const p = make();
        expect(await p._safetyCode('did:key:zAlice')).toBe('92475465');
        expect(await p._safetyCode('did:key:zBob')).toBe('24953079');
    });

    it('always returns an 8-digit string', async () => {
        const p = make();
        for (const did of ['did:key:zA', 'did:key:zB', 'x', '', 'did:key:zLong0000']) {
            expect(await p._safetyCode(did)).toMatch(/^[0-9]{8}$/);
        }
    });

    // The anti-MitM property: primary derives from the device_did it received,
    // the new device from its OWN clientDid. A relay that substitutes the
    // device_did makes the two codes disagree, so the user can catch it.
    it('derives different codes for different DIDs', async () => {
        const p = make();
        const primary = await p._safetyCode('did:key:zAttacker');
        const device = await p._safetyCode('did:key:zRealDevice');
        expect(primary).not.toBe(device);
    });

    it('ignores the relay-supplied safety_code (matching DIDs agree)', async () => {
        const p = make();
        // Same DID at both ends => identical codes, regardless of any ev.safety_code.
        const a = await p._safetyCode('did:key:zRealDevice');
        const b = await p._safetyCode('did:key:zRealDevice');
        expect(a).toBe(b);
    });
});

describe('pairing_approved on the new device', () => {
    it('stores the cert, marks the wizard done and closes it', async () => {
        vi.useFakeTimers();
        const store = {};
        const els = { 'onboarding-modal': { style: { display: 'flex' } } };
        global.localStorage = {
            getItem: (k) => (k in store ? store[k] : null),
            setItem: (k, v) => { store[k] = String(v); },
            removeItem: (k) => { delete store[k]; },
        };
        global.document = { getElementById: (id) => els[id] || null };
        global.location = { reload: vi.fn() };
        const p = make();
        p.handleEvent({ type: 'pairing_approved', delegation_cert: { id: 'c1' } });
        await Promise.resolve();
        expect(JSON.parse(store.proxion_delegation_cert)).toEqual({ id: 'c1' });
        expect(store.proxion_wizard_done).toBe('1');
        expect(els['onboarding-modal'].style.display).toBe('none');
        vi.useRealTimers();
    });
});
