// E2E status UI — the DM header encryption badge, the safety-number fingerprint
// bar, and the verify modal. Pure presentation over e2e.js primitives +
// localStorage verification flags; no socket/view coupling.
//
// createE2EStatus() — no host deps. e2eSupported/isE2EEnabled/myX25519PubB64u/
// safetyNumber come from e2e.js. The DID currently shown in the fingerprint bar
// is cluster-owned state (state._fingerprintBarDid); the "Mark as verified"
// button handler in main.js reads it back via e2eStatus.state.

import { e2eSupported, isE2EEnabled, isPeerVerified, myX25519PubB64u, safetyNumber, e2eScopedKey } from './e2e.js';
import { t } from './i18n.js';

export function createE2EStatus() {
    const state = {
        _fingerprintBarDid: null, // R11.2.2: peer whose safety number the bar shows
    };

    function _updateE2EStatus(peerId) {
        const el = document.getElementById('dm-e2e-status');
        if (!el) return;
        const btn = document.getElementById('dm-e2e-verify-btn');
        if (peerId && isE2EEnabled(peerId)) {
            const verified = isPeerVerified(peerId);
            // Encrypted: a closed lock. Verified: a shield with a check.
            el.innerHTML = (verified ? '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" focusable="false" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z"/></svg>' : '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" focusable="false" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25z"/></svg>') + ' E2E';
            el.title = verified ? t('e2e.tip.verified') : t('e2e.tip.encrypted');
            el.dataset.state = verified ? 'verified' : 'encrypted';
            el.style.display = 'inline';
            // Nothing left to verify once verified.
            if (btn) btn.style.display = verified ? 'none' : 'inline';
        } else if (peerId && e2eSupported) {
            // Open lock plus the words; on phones CSS keeps only the lock visible
            // (the words stay for screen readers) so the call buttons still fit.
            const _esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
            el.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" focusable="false" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M13.5 10.5V6.75a4.5 4.5 0 1 1 9 0v3.75M3.75 21.75h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H3.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25z"/></svg> <span class="e2e-label">' + _esc(t('e2e.none')) + '</span>';
            el.title = t('e2e.tip.notExchanged');
            // Not encrypted yet: neutral, never the green the encrypted state uses.
            el.dataset.state = 'none';
            el.style.display = 'inline';
            if (btn) btn.style.display = 'none';
        } else {
            el.style.display = 'none';
            if (btn) btn.style.display = 'none';
        }
    }

    // The safety-number bar shows the same number as the Verify dialog: one
    // number per contact, computed here from the two E2E keys, so the gateway
    // has no say in it. Hidden until both keys are known.
    async function _updateIdentityFingerprint(peerId) {
        const bar = document.getElementById("fingerprint-bar");
        const wordsEl = document.getElementById("fingerprint-words");
        const verifyBtn = document.getElementById("fingerprint-verify-btn");
        if (!bar || !wordsEl || !verifyBtn) return;
        const myPub = myX25519PubB64u();
        const theirPub = peerId ? localStorage.getItem(e2eScopedKey('proxion_e2e_peer_pub_' + peerId)) : null;
        state._fingerprintBarDid = peerId || null;
        if (!peerId || !myPub || !theirPub) {
            bar.style.display = "none";
            return;
        }
        let sn;
        try { sn = await safetyNumber(myPub, theirPub); } catch (_) { bar.style.display = "none"; return; }
        if (state._fingerprintBarDid !== peerId) return;   // another thread opened meanwhile
        wordsEl.textContent = sn;
        if (isPeerVerified(peerId)) {
            verifyBtn.textContent = '✓ ' + t('e2e.verified');
            verifyBtn.style.background = "var(--color-success-tint)";
            verifyBtn.style.color = "var(--color-success-soft)";
            verifyBtn.disabled = true;
        } else {
            verifyBtn.textContent = t('e2e.markVerified');
            verifyBtn.style.background = "var(--surface-2)";
            verifyBtn.style.color = "var(--text-secondary)";
            verifyBtn.disabled = false;
        }
        bar.style.display = "flex";
    }

    async function _openVerifyModal(peerId) {
        const myPub   = myX25519PubB64u();
        const theirPub = localStorage.getItem(e2eScopedKey('proxion_e2e_peer_pub_' + peerId));
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
