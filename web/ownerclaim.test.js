// Gateway owner claim: the #claim= link, the banner, and the owner list.
import { describe, it, expect, vi } from 'vitest';
import { takeClaimFragment, createOwnerClaim } from './ownerclaim.js';
import { escHtml } from './util.js';

const CODE = 'Abc_def-123456789012345678901234';

function loc(hash, pathname = '/', search = '') {
    return { hash, pathname, search };
}

describe('takeClaimFragment', () => {
    it('returns the code and strips the fragment, keeping path and query', () => {
        const hist = { state: { a: 1 }, replaceState: vi.fn() };
        expect(takeClaimFragment(loc('#claim=' + CODE, '/app', '?x=1'), hist)).toBe(CODE);
        expect(hist.replaceState).toHaveBeenCalledWith({ a: 1 }, '', '/app?x=1');
    });

    it('leaves other fragments and plain addresses alone', () => {
        const hist = { replaceState: vi.fn() };
        expect(takeClaimFragment(loc(''), hist)).toBe('');
        expect(takeClaimFragment(loc('#room=1'), hist)).toBe('');
        expect(hist.replaceState).not.toHaveBeenCalled();
    });

    it('strips a malformed code but does not use it', () => {
        for (const bad of ['#claim=', '#claim=short', '#claim=%E0%A4%A', '#claim=has space in it 0000000',
            '#claim=' + 'x'.repeat(300), '#claim=<script>alert(1)</script>xxxx']) {
            const hist = { replaceState: vi.fn() };
            expect(takeClaimFragment(loc(bad), hist)).toBe('');
            expect(hist.replaceState).toHaveBeenCalledTimes(1);
        }
    });

    it('survives a history that cannot be rewritten', () => {
        const hist = { replaceState: () => { throw new Error('denied'); } };
        expect(takeClaimFragment(loc('#claim=' + CODE), hist)).toBe(CODE);
        expect(takeClaimFragment(loc('#claim=' + CODE), null)).toBe(CODE);
    });
});

function fakeDoc() {
    const els = {};
    const mk = () => {
        const cls = new Set();
        return {
            hidden: true, innerHTML: '', listeners: {},
            classList: { toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)), contains: (c) => cls.has(c) },
            addEventListener(ev, fn) { this.listeners[ev] = fn; },
            querySelectorAll: () => [],
        };
    };
    for (const id of ['owner-claim-banner', 'owner-claim-btn', 'settings-owner-devices', 'settings-owner-devices-list']) {
        els[id] = mk();
    }
    return { els, getElementById: (id) => els[id] || null };
}

function make(over = {}) {
    const doc = over.doc || fakeDoc();
    const sent = [];
    const toasts = [];
    const oc = createOwnerClaim({
        send: (m) => sent.push(m),
        showToast: (m) => toasts.push(m),
        showConfirm: over.showConfirm || vi.fn(),
        showPromptModal: over.showPromptModal || (async () => null),
        t: (k) => k,
        escHtml,
        initialCode: over.initialCode || '',
        doc,
    });
    const bannerOn = () => doc.els['owner-claim-banner'].classList.contains('visible');
    return { oc, doc, sent, toasts, bannerOn };
}

describe('createOwnerClaim', () => {
    it('presents a link code once after sign-in, without the banner', () => {
        const { oc, sent, bannerOn } = make({ initialCode: CODE });
        oc.onRegistered({ webid: 'did:key:zMe', owner: false, claimable: true });
        expect(sent).toEqual([{ cmd: 'claim_gateway', code: CODE }]);
        expect(bannerOn()).toBe(false);
        // A reconnect does not send it again.
        oc.onRegistered({ webid: 'did:key:zMe', owner: false, claimable: true });
        expect(sent).toHaveLength(1);
        expect(bannerOn()).toBe(true);
    });

    it('drops the link code when the session is already an owner', () => {
        const { oc, sent, doc } = make({ initialCode: CODE });
        oc.onRegistered({ webid: 'did:key:zMe', owner: true });
        expect(sent).toEqual([]);
        expect(oc.hasPending()).toBe(false);
        expect(doc.els['settings-owner-devices'].hidden).toBe(false);
    });

    it('shows the banner only on an unclaimed gateway', () => {
        const { oc, bannerOn } = make();
        oc.onRegistered({ webid: 'did:key:zMe', owner: false });
        expect(bannerOn()).toBe(false);
        oc.onRegistered({ webid: 'did:key:zMe', owner: false, claimable: true });
        expect(bannerOn()).toBe(true);
    });

    it('the banner button asks for the code and sends it trimmed', async () => {
        const prompt = vi.fn(async () => `  ${CODE}  `);
        const { doc, sent } = make({ showPromptModal: prompt });
        await doc.els['owner-claim-btn'].listeners.click();
        await Promise.resolve();
        expect(prompt).toHaveBeenCalled();
        expect(prompt.mock.calls[0][1].type).toBe('password');
        expect(sent).toEqual([{ cmd: 'claim_gateway', code: CODE }]);
    });

    it('a cancelled prompt sends nothing', async () => {
        const { oc, sent } = make({ showPromptModal: async () => null });
        await oc.promptForCode();
        expect(sent).toEqual([]);
    });

    it('a successful claim hides the banner and makes the owner list visible', () => {
        const { oc, doc, toasts, bannerOn } = make();
        oc.onRegistered({ webid: 'did:key:zMe', owner: false, claimable: true });
        oc.onClaimed({ type: 'gateway_claimed' });
        expect(bannerOn()).toBe(false);
        expect(doc.els['settings-owner-devices'].hidden).toBe(false);
        expect(toasts).toEqual(['claim.done']);
        expect(oc.isOwner()).toBe(true);
    });

    it('a failure explains itself and keeps the banner for a retry', () => {
        const { oc, toasts, bannerOn } = make();
        oc.onClaimFailed({ reason: 'invalid_code' });
        expect(bannerOn()).toBe(true);
        oc.onClaimFailed({ reason: 'rate_limited' });
        oc.onClaimFailed({ reason: 'already_claimed' });
        expect(bannerOn()).toBe(false);
        expect(toasts).toEqual(['claim.failed.invalid', 'claim.failed.rateLimited', 'claim.failed.alreadyClaimed']);
    });

    it('asks for the owner list only as an owner', () => {
        const { oc, sent } = make();
        oc.onRegistered({ webid: 'did:key:zMe', owner: false });
        oc.requestOwnerDevices();
        expect(sent).toEqual([]);
        oc.onRegistered({ webid: 'did:key:zMe', owner: true });
        oc.requestOwnerDevices();
        expect(sent).toEqual([{ cmd: 'list_owner_devices' }]);
    });

    it('renders the owner list escaped and marks this device', () => {
        const { oc, doc } = make();
        oc.onRegistered({ webid: 'did:key:zMe', owner: true });
        oc.renderOwnerDevices({ devices: [
            { did: 'did:key:zMe', display_name: 'Me', self: true },
            { did: 'did:key:zOther"><img>', display_name: '<b>x</b>', self: false },
        ] });
        const html = doc.els['settings-owner-devices-list'].innerHTML;
        expect(html).toContain('owners.thisDevice');
        expect(html).toContain('&lt;b&gt;x&lt;/b&gt;');
        expect(html).not.toContain('<b>x</b>');
        expect(html).not.toContain('"><img>');
    });

    it('removing another owner sends at once; removing yourself asks first', () => {
        const confirm = vi.fn((msg, ok) => ok());
        const list = { innerHTML: '', buttons: [], querySelectorAll() { return this.buttons; } };
        const doc = fakeDoc();
        doc.els['settings-owner-devices-list'] = list;
        const { oc, sent } = make({ showConfirm: confirm, doc });
        oc.onRegistered({ webid: 'did:key:zMe', owner: true });
        sent.length = 0;
        const mkBtn = (did) => ({ dataset: { ownerDid: did }, addEventListener(ev, fn) { this.click = fn; } });
        list.buttons = [mkBtn('did:key:zOther'), mkBtn('did:key:zMe')];
        oc.renderOwnerDevices({ devices: [{ did: 'did:key:zOther' }, { did: 'did:key:zMe', self: true }] });
        list.buttons[0].click();
        expect(confirm).not.toHaveBeenCalled();
        expect(sent).toEqual([{ cmd: 'remove_owner_device', did: 'did:key:zOther' }]);
        list.buttons[1].click();
        expect(confirm).toHaveBeenCalledTimes(1);
        expect(confirm.mock.calls[0][0]).toBe('owners.removeSelf');
        expect(sent[1]).toEqual({ cmd: 'remove_owner_device', did: 'did:key:zMe', confirm: true });
    });

    it('the last owner removing itself gets the stronger warning', () => {
        const confirm = vi.fn();
        const list = { innerHTML: '', buttons: [], querySelectorAll() { return this.buttons; } };
        const doc = fakeDoc();
        doc.els['settings-owner-devices-list'] = list;
        const { oc } = make({ showConfirm: confirm, doc });
        oc.onRegistered({ webid: 'did:key:zMe', owner: true });
        const btn = { dataset: { ownerDid: 'did:key:zMe' }, addEventListener(ev, fn) { this.click = fn; } };
        list.buttons = [btn];
        oc.renderOwnerDevices({ devices: [{ did: 'did:key:zMe', self: true }] });
        btn.click();
        expect(confirm.mock.calls[0][0]).toBe('owners.removeSelfLast');
    });
});
