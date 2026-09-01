import { describe, it, expect } from 'vitest';
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
    //   int(sha256(did).hexdigest()[:8], 16) % 1000000, zero-padded to 6.
    it('matches the Python formula for known DIDs', async () => {
        const p = make();
        expect(await p._safetyCode('did:key:zAlice')).toBe('475465');
        expect(await p._safetyCode('did:key:zBob')).toBe('953079');
    });

    it('always returns a 6-digit string', async () => {
        const p = make();
        for (const did of ['did:key:zA', 'did:key:zB', 'x', '', 'did:key:zLong0000']) {
            expect(await p._safetyCode(did)).toMatch(/^[0-9]{6}$/);
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
