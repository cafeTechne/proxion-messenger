import { describe, it, expect, beforeEach } from 'vitest';
import {
    CODE_ALPHABET, generateRecoveryCode, normalizeRecoveryCode,
    passphraseFromInput, createRecovery,
} from './recovery.js';

describe('generateRecoveryCode', () => {
    it('matches the XXXX-XXXX-XXXX-XXXX-XXXX format', () => {
        const re = new RegExp(`^([${CODE_ALPHABET}]{4}-){4}[${CODE_ALPHABET}]{4}$`);
        for (let i = 0; i < 50; i++) expect(generateRecoveryCode()).toMatch(re);
    });

    it('never contains ambiguous characters', () => {
        const banned = /[01ILOU]/;
        for (let i = 0; i < 50; i++) expect(generateRecoveryCode()).not.toMatch(banned);
    });

    it('generates distinct codes', () => {
        const seen = new Set();
        for (let i = 0; i < 100; i++) seen.add(generateRecoveryCode());
        expect(seen.size).toBe(100);
    });

    it('uses the whole alphabet over many draws (no biased truncation)', () => {
        const seen = new Set();
        for (let i = 0; i < 200; i++) {
            for (const c of generateRecoveryCode().replace(/-/g, '')) seen.add(c);
        }
        expect(seen.size).toBe(CODE_ALPHABET.length);
    });
});

describe('normalizeRecoveryCode', () => {
    it('canonicalizes case and separators', () => {
        expect(normalizeRecoveryCode('abcd-efgh-jkmn-pqrs-tvwx'))
            .toBe('ABCD-EFGH-JKMN-PQRS-TVWX');
        expect(normalizeRecoveryCode('ABCD EFGH JKMN PQRS TVWX'))
            .toBe('ABCD-EFGH-JKMN-PQRS-TVWX');
        expect(normalizeRecoveryCode('abcdefghjkmnpqrstvwx'))
            .toBe('ABCD-EFGH-JKMN-PQRS-TVWX');
    });

    it('round-trips generated codes', () => {
        for (let i = 0; i < 20; i++) {
            const code = generateRecoveryCode();
            expect(normalizeRecoveryCode(code.toLowerCase())).toBe(code);
        }
    });

    it('rejects non-code input (wrong length or ambiguous chars)', () => {
        expect(normalizeRecoveryCode('my secret passphrase')).toBeNull();
        expect(normalizeRecoveryCode('')).toBeNull();
        expect(normalizeRecoveryCode(null)).toBeNull();
        // Right length, but O and I are not in the alphabet.
        expect(normalizeRecoveryCode('OOOO-IIII-JKMN-PQRS-TVWX')).toBeNull();
    });
});

describe('passphraseFromInput', () => {
    it('canonicalizes code-shaped input', () => {
        expect(passphraseFromInput('abcd efgh jkmn pqrs tvwx'))
            .toBe('ABCD-EFGH-JKMN-PQRS-TVWX');
    });
    it('passes free-form passphrases through verbatim', () => {
        expect(passphraseFromInput('correct horse battery staple'))
            .toBe('correct horse battery staple');
    });
});

// ── Modal flow (DOM stubs, same style as onboarding.test.js) ────────────────
let els;
function mkEl(over = {}) {
    const el = {
        value: '', textContent: '', checked: false, disabled: false,
        style: {}, classList: { add() {}, remove() {} },
        handlers: {},
        addEventListener(ev, fn) { this.handlers[ev] = fn; },
        focus() {}, click() {}, ...over,
    };
    return el;
}

beforeEach(() => {
    els = {};
    global.document = {
        getElementById: (id) => (els[id] ||= mkEl()),
        querySelector: () => null,
    };
    const store = {};
    global.localStorage = {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: (k) => { delete store[k]; },
    };
});

describe('recovery-kit modal', () => {
    it('openKitModal shows a fresh code with download disabled until confirmed', () => {
        const recovery = createRecovery({ showToast: () => {}, showPromptModal: async () => null });
        recovery.wireRecovery({});
        recovery.openKitModal();

        const code = els['recovery-code-display'].textContent;
        expect(code).toMatch(/^([2-9A-HJKMNP-TV-Z]{4}-){4}[2-9A-HJKMNP-TV-Z]{4}$/);
        expect(els['recovery-confirm-saved'].checked).toBe(false);
        expect(els['recovery-download-btn'].disabled).toBe(true);
        expect(els['recovery-kit-modal'].style.display).toBe('flex');

        // Confirming the checkbox enables download.
        els['recovery-confirm-saved'].handlers['change']({ target: { checked: true } });
        expect(els['recovery-download-btn'].disabled).toBe(false);

        // Cancel clears the displayed code (shown only once).
        els['recovery-cancel-btn'].handlers['click']();
        expect(els['recovery-kit-modal'].style.display).toBe('none');
        expect(els['recovery-code-display'].textContent).toBe('');
    });

    it('regenerates a different code on each open', () => {
        const recovery = createRecovery({ showToast: () => {}, showPromptModal: async () => null });
        recovery.openKitModal();
        const first = els['recovery-code-display'].textContent;
        recovery.openKitModal();
        expect(els['recovery-code-display'].textContent).not.toBe(first);
    });
});
