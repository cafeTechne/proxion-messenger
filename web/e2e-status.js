// E2E status UI — the DM header encryption badge, the safety-number fingerprint
// bar, and the verify modal. Pure presentation over e2e.js primitives +
// localStorage verification flags; no socket/view coupling.
//
// createE2EStatus() — no host deps. e2eSupported/isE2EEnabled/myX25519PubB64u/
// safetyNumber come from e2e.js. The DID currently shown in the fingerprint bar
// is cluster-owned state (state._fingerprintBarDid); the "Mark as verified"
// button handler in main.js reads it back via e2eStatus.state.

import { e2eSupported, isE2EEnabled, myX25519PubB64u, safetyNumber } from './e2e.js';
import { t } from './i18n.js';

export function createE2EStatus() {
    const state = {
        _fingerprintBarDid: null, // R11.2.2: DID shown in fingerprint bar
    };

    function _updateE2EStatus(peerId) {
        const el = document.getElementById('dm-e2e-status');
        if (!el) return;
        const btn = document.getElementById('dm-e2e-verify-btn');
        if (peerId && isE2EEnabled(peerId)) {
            const verified = localStorage.getItem('proxion_e2e_verified_' + peerId) === '1';
            el.innerHTML = verified ? '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25z"/></svg> E2E' : '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M13.5 10.5V6.75a4.5 4.5 0 1 1 9 0v3.75M3.75 21.75h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H3.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25z"/></svg> E2E';
            el.title = verified ? 'End-to-end encrypted (verified)' : 'End-to-end encrypted (tap Verify to confirm identity)';
            el.style.display = 'inline';
            if (btn) btn.style.display = 'inline';
        } else if (peerId && e2eSupported) {
            el.textContent = 'No E2E';
            el.title = 'E2E key not yet exchanged — send a message first';
            el.style.display = 'inline';
            if (btn) btn.style.display = 'none';
        } else {
            el.style.display = 'none';
            if (btn) btn.style.display = 'none';
        }
    }

    async function _updateIdentityFingerprint(peerDid) {
        const bar = document.getElementById("fingerprint-bar");
        const wordsEl = document.getElementById("fingerprint-words");
        const verifyBtn = document.getElementById("fingerprint-verify-btn");
        if (!bar || !wordsEl || !verifyBtn) return;
        if (!peerDid || !peerDid.startsWith("did:key:")) {
            bar.style.display = "none";
            state._fingerprintBarDid = null;
            return;
        }
        state._fingerprintBarDid = peerDid;
        bar.style.display = "flex";
        wordsEl.textContent = t('e2e.loading');
        try {
            const resp = await fetch(`/fingerprint/${encodeURIComponent(peerDid)}`);
            if (!resp.ok) { bar.style.display = "none"; return; }
            const data = await resp.json();
            const words = (data.safety_words || []);
            wordsEl.textContent = words.slice(0,3).join(" ") + "  " + words.slice(3).join(" ");
            const verified = localStorage.getItem("proxion_verified_" + peerDid) === "1";
            if (verified) {
                verifyBtn.textContent = '✓ ' + t('e2e.verified');
                verifyBtn.style.background = "#134e26";
                verifyBtn.style.color = "#4ade80";
                verifyBtn.disabled = true;
            } else {
                verifyBtn.textContent = t('e2e.markVerified');
                verifyBtn.style.background = "#1e293b";
                verifyBtn.style.color = "#94a3b8";
                verifyBtn.disabled = false;
            }
        } catch (_) {
            bar.style.display = "none";
        }
    }

    async function _openVerifyModal(peerId) {
        const myPub   = myX25519PubB64u();
        const theirPub = localStorage.getItem('proxion_e2e_peer_pub_' + peerId);
        if (!myPub || !theirPub) return;

        const sn = await safetyNumber(myPub, theirPub);

        const shorten = s => s.slice(0, 12) + '…' + s.slice(-4);
        const modal = document.getElementById('e2e-verify-modal');
        document.getElementById('e2e-modal-my-key').textContent    = shorten(myPub);
        document.getElementById('e2e-modal-their-key').textContent  = shorten(theirPub);
        document.getElementById('e2e-modal-safety-number').textContent = sn;
        document.getElementById('e2e-modal-current-peer').value    = peerId;
        if (modal) modal.style.display = 'flex';
    }

    return { _updateE2EStatus, _updateIdentityFingerprint, _openVerifyModal, state };
}
