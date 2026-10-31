// msgrequests.js: one card per sender, Accept and Decline hand back the request.
import { describe, it, expect, vi } from 'vitest';
import { createMessageRequests, requestKey } from './msgrequests.js';

function fakeDoc() {
    const byId = {};
    const list = {
        children: [],
        appendChild(c) { this.children.push(c); byId[c.id] = c; c._parent = this; },
    };
    const section = { style: { display: 'none' } };
    byId['friend-request-list'] = list;
    byId['friend-requests-section'] = section;
    return {
        list, section,
        getElementById: (id) => byId[id] || null,
        createElement: () => ({
            id: '', dataset: {}, innerHTML: '',
            remove() { const i = list.children.indexOf(this); if (i >= 0) list.children.splice(i, 1); delete byId[this.id]; },
        }),
    };
}

// A click on a card's button, as the delegated listener sees it.
function click(doc, webid, action) {
    const card = doc.getElementById(requestKey(webid));
    const btn = { dataset: { mrAction: action }, closest: (sel) => (sel === '[data-mr-action]' ? btn : card) };
    return { target: { closest: () => btn } };
}

const BOB = 'https://pod.example/bob/profile/card#me';

describe('createMessageRequests', () => {
    it('shows one card per sender with the WebID, the name escaped and marked unverified', () => {
        const doc = fakeDoc();
        const mr = createMessageRequests({ doc });
        expect(mr.add({ from_webid: BOB, from_display_name: '<b>Bob</b>' })).toBe(true);
        expect(mr.add({ from_webid: BOB, from_display_name: 'Bob' })).toBe(false);
        expect(doc.list.children).toHaveLength(1);
        expect(doc.list.children[0].innerHTML).toContain(BOB);
        expect(doc.list.children[0].innerHTML).not.toContain('<b>Bob</b>');
        expect(doc.section.style.display).toBe('');
    });

    it('accept and decline hand back the request and remove the card', () => {
        const doc = fakeDoc();
        const onAccept = vi.fn(), onDecline = vi.fn();
        const mr = createMessageRequests({ doc, onAccept, onDecline });
        mr.add({ from_webid: BOB, from_display_name: 'Bob' });
        mr.handleClick(click(doc, BOB, 'accept'));
        expect(onAccept).toHaveBeenCalledWith({ from_webid: BOB, from_display_name: 'Bob' });
        expect(doc.getElementById(requestKey(BOB))).toBe(null);
        expect(mr.has(BOB)).toBe(false);
        mr.add({ from_webid: BOB });
        mr.handleClick(click(doc, BOB, 'decline'));
        expect(onDecline).toHaveBeenCalled();
        expect(doc.section.style.display).toBe('none');
    });
});
