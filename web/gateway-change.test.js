// gateway-change.test.js: reviewing a contact's unproven gateway address change.
import { describe, it, expect, beforeEach } from 'vitest';
import { createGatewayChanges } from './gateway-change.js';

// A small DOM stand-in: enough for createElement/append/replaceChildren and
// click handlers on the rendered buttons.
function node(tag) {
    const n = {
        tagName: tag, children: [], dataset: {}, hidden: false, textContent: '', className: '',
        _click: null,
        append(...c) { n.children.push(...c); },
        replaceChildren() { n.children = []; },
        addEventListener(type, fn) { if (type === 'click') n._click = fn; },
        click() { n._click?.(); },
    };
    return n;
}
function all(n) { return [n, ...n.children.flatMap(all)]; }
function text(n) { return all(n).map(c => c.textContent).join(' '); }

const ALICE = 'did:key:z6MkAlice';
const CHANGE = { peer_did: ALICE, display_name: 'Alice', old_url: 'https://a1.example', new_url: 'https://a2.example' };

let sent, toasts, confirms, bar, active;
function make() {
    sent = []; toasts = []; confirms = [];
    bar = node('div'); bar.hidden = true;
    const doc = { getElementById: (id) => (id === 'gateway-change-bar' ? bar : null), createElement: node };
    const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
    return createGatewayChanges({
        getSocket: () => socket,
        showConfirm: (msg, onOk, onCancel, opts) => confirms.push({ msg, onOk, opts }),
        showToast: (m) => toasts.push(m),
        getActivePeer: () => active,
        doc,
    });
}

beforeEach(() => { global.WebSocket = { OPEN: 1 }; active = null; });

describe('gateway address change review', () => {
    it('shows the old and new address and a warning on the contact DM only', () => {
        const g = make();
        active = ALICE;
        g.handleChangesEvent([CHANGE]);
        expect(bar.hidden).toBe(false);
        const s = text(bar);
        expect(s).toContain('https://a1.example');
        expect(s).toContain('https://a2.example');
        expect(s).toContain('gwchange.warning');
        g.renderBar('did:key:z6MkBob');
        expect(bar.hidden).toBe(true);
        expect(bar.children).toEqual([]);
    });

    it('approving asks first, warns, and only then sends the command', () => {
        const g = make();
        active = ALICE;
        g.handleChangesEvent([CHANGE]);
        all(bar).find(n => n.dataset.action === 'approve').click();
        expect(sent).toEqual([]);
        expect(confirms).toHaveLength(1);
        expect(confirms[0].opts.danger).toBe(true);
        confirms[0].onOk();
        expect(sent).toEqual([{ cmd: 'approve_peer_gateway_change', peer_did: ALICE }]);
    });

    it('reject sends the command for that contact', () => {
        const g = make();
        active = ALICE;
        g.handleChangesEvent([CHANGE]);
        all(bar).find(n => n.dataset.action === 'reject').click();
        expect(sent).toEqual([{ cmd: 'reject_peer_gateway_change', peer_did: ALICE }]);
    });

    it('the gateway list is authoritative: a resolved change disappears', () => {
        const g = make();
        active = ALICE;
        g.handleChangesEvent([CHANGE]);
        g.handleChangesEvent([]);
        expect(bar.hidden).toBe(true);
        expect(g.get(ALICE)).toBeNull();
        g.approve(ALICE);
        g.reject(ALICE);
        expect(confirms).toEqual([]);
        expect(sent).toEqual([]);
    });

    it('announces each pending change once and ignores malformed rows', () => {
        const g = make();
        g.handleChangesEvent([CHANGE, null, { peer_did: 5 }]);
        g.handleChangesEvent([CHANGE]);
        expect(toasts).toEqual(['gwchange.toast']);
        expect(g.get(ALICE).new_url).toBe('https://a2.example');
    });

    it('confirms the outcome', () => {
        const g = make();
        g.handleResult({ type: 'peer_gateway_change_approved', approved: true });
        g.handleResult({ type: 'peer_gateway_change_rejected', rejected: true });
        g.handleResult({ type: 'peer_gateway_change_approved', approved: false });
        expect(toasts).toEqual(['gwchange.approved', 'gwchange.rejected']);
    });
});
