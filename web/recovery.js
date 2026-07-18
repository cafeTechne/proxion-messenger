// recovery.js — identity recovery-kit UX (E1): generated recovery codes and
// the download / verify / restore flows around the gateway's /backup and
// /restore endpoints.
//
// A factory like the other modules: host helpers (showToast, showPromptModal)
// are injected; wireRecovery() attaches all listeners (settings buttons, the
// recovery-kit modal, the verify flow, and the onboarding entry points).
//
// Design note — generated codes, not user passphrases: the old flow asked the
// user to invent a passphrase, which in practice is weak and forgettable. We
// now generate a ~98-bit code from an unambiguous, language-neutral alphabet
// and make the user confirm they stored it before the kit downloads. "Use my
// own passphrase" remains as an escape hatch, and restore/verify still accept
// legacy free-form passphrases.

import { t } from './i18n.js';

// Digits + uppercase minus lookalikes (0, 1, I, L, O, U): 30 chars.
export const CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ";
const CODE_GROUPS = 5;
const GROUP_LEN = 4;

// XXXX-XXXX-XXXX-XXXX-XXXX — 20 chars × log2(30) ≈ 98 bits of entropy.
export function generateRecoveryCode() {
    const need = CODE_GROUPS * GROUP_LEN;
    const chars = [];
    while (chars.length < need) {
        const buf = new Uint8Array(need * 2);
        crypto.getRandomValues(buf);
        for (const b of buf) {
            if (chars.length >= need) break;
            // Rejection-sample: 240 = 8×30, so b % 30 is unbiased below 240.
            if (b < 240) chars.push(CODE_ALPHABET[b % 30]);
        }
    }
    const groups = [];
    for (let i = 0; i < CODE_GROUPS; i++) {
        groups.push(chars.slice(i * GROUP_LEN, (i + 1) * GROUP_LEN).join(""));
    }
    return groups.join("-");
}

// Returns the canonical XXXX-XXXX-… form if the input is a recovery code
// (any case, any/no separators), or null if it isn't one — callers then treat
// the input as a legacy free-form passphrase and send it verbatim.
export function normalizeRecoveryCode(raw) {
    const stripped = (raw || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
    if (stripped.length !== CODE_GROUPS * GROUP_LEN) return null;
    for (const c of stripped) {
        if (!CODE_ALPHABET.includes(c)) return null;
    }
    const groups = [];
    for (let i = 0; i < CODE_GROUPS; i++) {
        groups.push(stripped.slice(i * GROUP_LEN, (i + 1) * GROUP_LEN));
    }
    return groups.join("-");
}

// Recovery code or legacy passphrase → the exact string to send the gateway.
export function passphraseFromInput(raw) {
    return normalizeRecoveryCode(raw) ?? raw;
}

export function createRecovery({ showToast, showPromptModal }) {
    let _code = null;

    function _authHeaders(extra = {}) {
        const apiToken = document.querySelector('meta[name="x-api-token"]')?.content || '';
        const headers = { ...extra };
        if (apiToken) headers['Authorization'] = 'Bearer ' + apiToken;
        return headers;
    }

    function _markBackedUp() {
        localStorage.setItem('proxion_backup_downloaded', Date.now().toString());
        document.getElementById('backup-nudge')?.classList.remove('visible');
    }

    async function _downloadKit(passphrase) {
        try {
            const resp = await fetch('/backup?passphrase=' + encodeURIComponent(passphrase),
                { headers: _authHeaders() });
            if (!resp.ok) { showToast(t('backup.failed', { status: resp.status })); return false; }
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const d = new Date();
            const stamp = d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
            a.href = url;
            a.download = `proxion-recovery-kit-${stamp}.json`;
            a.click();
            URL.revokeObjectURL(url);
            _markBackedUp();
            showToast(t('backup.downloaded'));
            return true;
        } catch (e) {
            showToast(t('backup.error', { error: e.message }));
            return false;
        }
    }

    // ── Recovery-kit modal (generated-code flow) ────────────────────────────
    function openKitModal() {
        _code = generateRecoveryCode();
        const codeEl = document.getElementById('recovery-code-display');
        if (codeEl) codeEl.textContent = _code;
        const cb = document.getElementById('recovery-confirm-saved');
        if (cb) cb.checked = false;
        _setDownloadEnabled(false);
        const modal = document.getElementById('recovery-kit-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _closeKitModal() {
        const modal = document.getElementById('recovery-kit-modal');
        if (modal) modal.style.display = 'none';
        // Drop the code from the DOM once the modal closes — shown only once.
        const codeEl = document.getElementById('recovery-code-display');
        if (codeEl) codeEl.textContent = '';
        _code = null;
    }

    function _setDownloadEnabled(on) {
        const btn = document.getElementById('recovery-download-btn');
        if (!btn) return;
        btn.disabled = !on;
        btn.style.opacity = on ? '1' : '0.45';
        btn.style.cursor = on ? 'pointer' : 'not-allowed';
    }

    // ── Verify (dry-run restore: proves the kit + code decrypt, no key change) ──
    async function _verifyKit(file) {
        const raw = await showPromptModal(t('recovery.verifyPrompt'), { type: 'password' });
        if (!raw) return;
        const pp = passphraseFromInput(raw);
        try {
            const data = await file.arrayBuffer();
            const resp = await fetch('/restore?dry_run=1&passphrase=' + encodeURIComponent(pp), {
                method: 'POST',
                headers: _authHeaders({ 'Content-Type': 'application/json' }),
                body: data,
            });
            const body = await resp.json().catch(() => ({}));
            if (resp.ok && body.valid) showToast(t('recovery.verifyOk'));
            else showToast(t('recovery.verifyFail', { error: body.error || String(resp.status) }));
        } catch (e) {
            showToast(t('recovery.verifyFail', { error: e.message }));
        }
    }

    // ── Restore (shared by the settings button and the onboarding entry) ────
    async function _restoreKit(file, getSocket) {
        const raw = await showPromptModal(t('recovery.restorePrompt'), { type: 'password' });
        if (!raw) return;
        const pp = passphraseFromInput(raw);
        try {
            const data = await file.arrayBuffer();
            const resp = await fetch('/restore?passphrase=' + encodeURIComponent(pp), {
                method: 'POST',
                headers: _authHeaders({ 'Content-Type': 'application/json' }),
                body: data,
            });
            if (!resp.ok) { showToast(t('restore.failed', { status: resp.status })); return; }
            showToast(t('restore.done'));
            setTimeout(() => { getSocket?.()?.close(); }, 1000);
        } catch (err) {
            showToast(t('restore.error', { error: err.message }));
        }
    }

    function wireRecovery({ getSocket } = {}) {
        // Settings: download → generated-code modal
        document.getElementById('settings-backup-btn')?.addEventListener('click', openKitModal);

        // Modal internals
        document.getElementById('recovery-copy-btn')?.addEventListener('click', () => {
            if (!_code) return;
            navigator.clipboard.writeText(_code)
                .then(() => showToast(t('recovery.copied')))
                .catch(() => {});
        });
        document.getElementById('recovery-confirm-saved')?.addEventListener('change', (e) => {
            _setDownloadEnabled(e.target.checked);
        });
        document.getElementById('recovery-download-btn')?.addEventListener('click', async () => {
            if (!_code) return;
            if (await _downloadKit(_code)) _closeKitModal();
        });
        document.getElementById('recovery-custom-pp')?.addEventListener('click', async (e) => {
            e.preventDefault();
            _closeKitModal();
            const pp = await showPromptModal(t('prompt.choosePassphrase'), { type: 'password' });
            if (pp) await _downloadKit(pp);
        });
        document.getElementById('recovery-cancel-btn')?.addEventListener('click', _closeKitModal);

        // Settings: verify
        document.getElementById('settings-verify-btn')?.addEventListener('click', () => {
            document.getElementById('settings-verify-input')?.click();
        });
        document.getElementById('settings-verify-input')?.addEventListener('change', async (e) => {
            const file = e.target.files?.[0];
            if (file) await _verifyKit(file);
            e.target.value = '';
        });

        // Settings: restore
        document.getElementById('settings-restore-btn')?.addEventListener('click', () => {
            document.getElementById('settings-restore-input')?.click();
        });
        document.getElementById('settings-restore-input')?.addEventListener('change', async (e) => {
            const file = e.target.files?.[0];
            if (file) await _restoreKit(file, getSocket);
            e.target.value = '';
        });

        // Onboarding: "Restore from a recovery kit" on the welcome step reuses
        // the settings restore input; "Save recovery kit" on the final step
        // opens the same generated-code modal.
        document.getElementById('ob-restore-kit')?.addEventListener('click', (e) => {
            e.preventDefault();
            document.getElementById('settings-restore-input')?.click();
        });
        document.getElementById('ob-save-kit-btn')?.addEventListener('click', openKitModal);
    }

    return { openKitModal, wireRecovery };
}
