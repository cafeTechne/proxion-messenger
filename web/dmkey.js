// dmkey.js: decide how a DM may be sent when the peer's encryption key matters.
//
// A DM is sent end-to-end encrypted or not at all. If no X25519 key is cached
// for the peer, the caller first tries to obtain one (the peer's pod in the
// gateway-free build, the gateway's device roster otherwise). If that fails the
// send is refused, unless the user explicitly chose, for this peer, to send
// unencrypted (for example to a bot or AI agent that cannot do E2E). That choice
// is stored per peer and is only consulted while no key is known: once a key
// arrives, messages are encrypted again.
import { e2eScopedKey } from './e2e.js';

export const PEER_KEY_WAIT_MS = 4000;

const _optInKey = (peerId) => e2eScopedKey('proxion_dm_plaintext_ok_' + peerId);

export function isPlaintextOptedIn(peerId) {
    if (!peerId) return false;
    try { return localStorage.getItem(_optInKey(peerId)) === '1'; } catch { return false; }
}

export function setPlaintextOptIn(peerId, on) {
    if (!peerId) return;
    try {
        if (on) localStorage.setItem(_optInKey(peerId), '1');
        else localStorage.removeItem(_optInKey(peerId));
    } catch { /* storage unavailable: the choice lasts for this send only */ }
}

// Resolve the send mode for a DM to peerId:
//   'encrypt'   a key is cached (possibly after obtainKey fetched it)
//   'plaintext' no key, and the user opted in to unencrypted messages for this peer
//   'refuse'    no key and no opt-in: keep the message, tell the user
// hasKey() is synchronous; obtainKey() may be async and may fail.
export async function resolveDmSendMode(peerId, { hasKey, obtainKey, optedIn = isPlaintextOptedIn }) {
    if (peerId && hasKey(peerId)) return 'encrypt';
    if (peerId && obtainKey) {
        try { await obtainKey(peerId); } catch { /* treated as not available */ }
        if (hasKey(peerId)) return 'encrypt';
    }
    return optedIn(peerId) ? 'plaintext' : 'refuse';
}

// The account-level (primary device) key in a gateway device roster. The gateway
// records the primary device under the account id itself (_gateway_auth.py).
export function primaryKeyFromDevices(peerId, devices) {
    if (!peerId || !Array.isArray(devices)) return null;
    const d = devices.find((x) => x && x.device_id === peerId && typeof x.pub_b64u === 'string' && x.pub_b64u);
    return d ? d.pub_b64u : null;
}

// Waits for a peer_device_keys reply for one peer. resolve() is called from the
// event handler; wait() settles with the devices, or null on timeout.
export function createDeviceKeyWaiter() {
    const waiting = new Map();   // peerId -> [resolve]
    return {
        wait(peerId, ms = PEER_KEY_WAIT_MS) {
            return new Promise((res) => {
                const list = waiting.get(peerId) || [];
                const done = (v) => { clearTimeout(timer); res(v); };
                const timer = setTimeout(() => {
                    const l = waiting.get(peerId) || [];
                    const i = l.indexOf(done);
                    if (i >= 0) l.splice(i, 1);
                    res(null);
                }, ms);
                list.push(done);
                waiting.set(peerId, list);
            });
        },
        resolve(peerId, devices) {
            const list = waiting.get(peerId);
            if (!list) return;
            waiting.delete(peerId);
            for (const done of list) done(devices || []);
        },
    };
}

export class DmKeyMissingError extends Error {
    constructor(peerId) { super('No encryption key for peer'); this.name = 'DmKeyMissingError'; this.peerId = peerId; }
}
