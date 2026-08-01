// solidchat-ui.test.js — the Solid-conversation panel. DOM-stubbed; the focus is
// that content from OTHER apps is rendered safely and the list/thread behave.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createSolidChatUI, shortWebId } from './solidchat-ui.js';

// Minimal element stub. innerHTML is a plain string; textContent is tracked
// separately so we can prove content never reaches innerHTML.
function mkEl() {
    const el = {
        className: '', type: '', _text: '', innerHTML: '', dataset: {}, _children: [],
        set textContent(v) { this._text = String(v); },
        get textContent() { return this._text; },
        appendChild(c) { c._parent = this; this._children.push(c); return c; },
        append(...cs) { cs.forEach(c => { if (c) c._parent = this; }); this._children.push(...cs); },
        addEventListener(ev, fn) { this._on = this._on || {}; this._on[ev] = fn; },
        remove() { const p = this._parent; if (p) p._children = p._children.filter(c => c !== this); },
        querySelector() { return null; },
    };
    return el;
}

beforeEach(() => {
    global.document = { createElement: () => mkEl() };
});

function fakeModel(over = {}) {
    return {
        listConversations: over.listConversations || (() => []),
        loadConversation: over.loadConversation || (async () => []),
        subscribeConversation: over.subscribeConversation || (() => () => {}),
        sendMessage: over.sendMessage || (async () => true),
        hostConversation: over.hostConversation || (async () => ({ id: 'https://me.pod/x/', role: 'host' })),
        joinConversation: over.joinConversation || (async () => ({ id: 'https://a.pod/x/', role: 'participant' })),
        discoverChats: over.discoverChats || (async () => []),
        listContacts: over.listContacts || (async () => []),
        listInvitations: over.listInvitations || (async () => []),
        acceptInvitation: over.acceptInvitation || (async () => ({ id: 'https://a.pod/x/', role: 'participant' })),
        dismissInvitation: over.dismissInvitation || (async () => true),
    };
}

// Recursively collect textContent + innerHTML from an element tree.
function harvest(el, acc = { text: [], html: [] }) {
    if (!el) return acc;
    if (el._text) acc.text.push(el._text);
    if (el.innerHTML) acc.html.push(el.innerHTML);
    (el._children || []).forEach(c => harvest(c, acc));
    return acc;
}

describe('shortWebId', () => {
    it('renders a readable, non-spoofable label', () => {
        expect(shortWebId('https://alice.pod/alice/profile/card#me')).toBe('alice@alice.pod');
        expect(shortWebId('https://x.pod/profile/card#me')).toBe('x.pod');
        expect(shortWebId('garbage')).toBe('garbage');
    });
});

describe('renderList', () => {
    it('shows an empty state when there are no conversations', () => {
        const ui = createSolidChatUI({ model: fakeModel() });
        const list = mkEl();
        ui.renderList(list, () => {});
        expect(harvest(list).text.join(' ')).toMatch(/no solid conversations/i);
    });

    it('renders one item per conversation with its title as text, not html', () => {
        const convs = [{ id: 'https://a.pod/x/', title: '<img src=x onerror=alert(1)>', role: 'host' }];
        const ui = createSolidChatUI({ model: fakeModel({ listConversations: () => convs }) });
        const list = mkEl();
        ui.renderList(list, () => {});
        const h = harvest(list);
        // The hostile title appears as text, and NOWHERE as innerHTML markup.
        expect(h.text.join(' ')).toContain('<img src=x onerror=alert(1)>');
        expect(h.html.join(' ')).not.toContain('<img');
    });

    it('calls onOpen with the conversation id when an item is clicked', () => {
        const convs = [{ id: 'https://a.pod/x/', title: 'A', role: 'joined' }];
        const ui = createSolidChatUI({ model: fakeModel({ listConversations: () => convs }) });
        const list = mkEl();
        let opened = null;
        ui.renderList(list, (id) => { opened = id; });
        // list -> <li> -> <button>; the button carries the click handler.
        list._children[0]._children[0]._on.click();
        expect(opened).toBe('https://a.pod/x/');
    });
});

describe('renderMessages (XSS safety)', () => {
    it('renders hostile author and body as text only', () => {
        const ui = createSolidChatUI({ model: fakeModel(), getMyWebId: () => 'https://me.pod/#me' });
        const feed = mkEl();
        ui.renderMessages(feed, [
            { from_webid: 'https://evil.pod/#me', from_display_name: '<script>alert(1)</script>', content: '"><img src=x onerror=alert(1)>' },
        ]);
        const h = harvest(feed);
        expect(h.text.join(' ')).toContain('<script>alert(1)</script>');       // as text
        expect(h.text.join(' ')).toContain('"><img src=x onerror=alert(1)>');  // as text
        expect(h.html.join(' ')).not.toContain('<script');                     // never markup
        expect(h.html.join(' ')).not.toContain('<img');
    });

    it('marks our own messages distinctly', () => {
        const me = 'https://me.pod/profile/card#me';
        const ui = createSolidChatUI({ model: fakeModel(), getMyWebId: () => me });
        const feed = mkEl();
        ui.renderMessages(feed, [
            { from_webid: me, content: 'mine' },
            { from_webid: 'https://other.pod/#me', content: 'theirs' },
        ]);
        expect(feed._children[0].className).toContain('mine');
        expect(feed._children[1].className).not.toContain('mine');
    });
});

describe('openConversation', () => {
    it('loads history then subscribes for live updates', async () => {
        const subs = [];
        const model = fakeModel({
            loadConversation: async () => [{ message_id: 'm1', from_webid: 'https://a.pod/#me', content: 'hi' }],
            subscribeConversation: (id, cb) => { subs.push({ id, cb }); return () => {}; },
        });
        const ui = createSolidChatUI({ model, getMyWebId: () => 'https://me.pod/#me' });
        const feed = mkEl();
        await ui.openConversation('https://a.pod/x/', feed);
        expect(feed._children).toHaveLength(1);            // history rendered
        expect(subs).toHaveLength(1);                       // subscribed

        // A live message arrives.
        subs[0].cb([{ message_id: 'm2', from_webid: 'https://a.pod/#me', content: 'new one' }]);
        expect(feed._children).toHaveLength(2);
        expect(feed._children[1].dataset.mid).toBe('m2');
    });
});

describe('discover (Track F2)', () => {
    const flush = () => new Promise(r => setTimeout(r, 0));

    it("lists a WebID's chats and joins the chosen one", async () => {
        const joined = [];
        const ui = createSolidChatUI({
            model: fakeModel({
                discoverChats: async () => [{ container: 'https://a.pod/proxion/rooms/team/', title: 'Team' }],
                joinConversation: async (url) => ({ id: url, role: 'participant' }),
            }),
        });
        const list = mkEl();
        await ui.discover('https://a.pod/#me', list, (c) => joined.push(c));
        expect(harvest(list).text.join(' ')).toContain('Team');
        list._children[0]._children[0]._on.click();   // list -> li -> button
        await flush();
        expect(joined).toHaveLength(1);
        expect(joined[0].id).toBe('https://a.pod/proxion/rooms/team/');
    });

    it('shows an empty state when the WebID hosts no chats', async () => {
        const ui = createSolidChatUI({ model: fakeModel({ discoverChats: async () => [] }) });
        const list = mkEl();
        await ui.discover('https://a.pod/#me', list, () => {});
        expect(harvest(list).text.join(' ')).toMatch(/no chats found/i);
    });

    it('renders a hostile discovered title as text, never markup', async () => {
        const ui = createSolidChatUI({
            model: fakeModel({ discoverChats: async () => [{ container: 'https://a.pod/x/', title: '<img src=x onerror=alert(1)>' }] }),
        });
        const list = mkEl();
        await ui.discover('https://a.pod/#me', list, () => {});
        const h = harvest(list);
        expect(h.text.join(' ')).toContain('<img src=x onerror=alert(1)>');
        expect(h.html.join(' ')).not.toContain('<img');
    });
});

describe('populateContacts (Track F3)', () => {
    it('fills the datalist with contacts: WebID as value, name as label', async () => {
        const ui = createSolidChatUI({
            model: fakeModel({ listContacts: async () => [
                { webid: 'https://alice.pod/profile/card#me', name: 'Alice' },
                { webid: 'https://bob.pod/bob/profile/card#me', name: '' },
            ] }),
        });
        const dl = mkEl();
        await ui.populateContacts(dl);
        expect(dl._children).toHaveLength(2);
        expect(dl._children[0].value).toBe('https://alice.pod/profile/card#me');
        expect(dl._children[0].label).toBe('Alice');
        // No name -> a readable short label, never empty.
        expect(dl._children[1].value).toBe('https://bob.pod/bob/profile/card#me');
        expect(dl._children[1].label).toBe('bob@bob.pod');
    });

    it('sets a hostile contact name via label, never as markup', async () => {
        const ui = createSolidChatUI({
            model: fakeModel({ listContacts: async () => [
                { webid: 'https://x.pod/#me', name: '<img src=x onerror=alert(1)>' },
            ] }),
        });
        const dl = mkEl();
        await ui.populateContacts(dl);
        expect(dl._children[0].label).toBe('<img src=x onerror=alert(1)>');   // property, not parsed
        expect(dl.innerHTML).toBe('');                                        // never touched innerHTML
    });

    it('skips entries without a WebID and tolerates no contacts', async () => {
        const ui = createSolidChatUI({
            model: fakeModel({ listContacts: async () => [{ name: 'no webid' }, null] }),
        });
        const dl = mkEl();
        await ui.populateContacts(dl);
        expect(dl._children).toHaveLength(0);
    });
});

describe('renderInvitations (Track G2)', () => {
    const flush = () => new Promise(r => setTimeout(r, 0));

    it('lists invitations with sender + title as text, and accepts one', async () => {
        const accepted = [];
        const ui = createSolidChatUI({
            model: fakeModel({
                listInvitations: async () => [{ id: 'https://b.pod/inbox/n1', from: 'https://alice.pod/alice/profile/card#me', container: 'https://alice.pod/x/', title: 'Team' }],
                acceptInvitation: async (inv) => ({ id: inv.container, role: 'participant' }),
            }),
        });
        const list = mkEl();
        await ui.renderInvitations(list, (c) => accepted.push(c));
        const h = harvest(list);
        expect(h.text.join(' ')).toContain('Team');
        expect(h.text.join(' ')).toContain('alice@alice.pod');
        // li -> [label, Accept, Dismiss]; click Accept.
        list._children[0]._children[1]._on.click();
        await flush();
        expect(accepted).toHaveLength(1);
        expect(accepted[0].id).toBe('https://alice.pod/x/');
        expect(list._children).toHaveLength(0);   // row removed
    });

    it('dismisses an invitation without joining', async () => {
        const dismissed = [];
        const ui = createSolidChatUI({
            model: fakeModel({
                listInvitations: async () => [{ id: 'https://b.pod/inbox/n1', from: 'https://a.pod/#me', container: 'https://a.pod/x/', title: 'X' }],
                dismissInvitation: async (inv) => { dismissed.push(inv.id); return true; },
                acceptInvitation: async () => { throw new Error('should not accept'); },
            }),
        });
        const list = mkEl();
        await ui.renderInvitations(list, () => {});
        list._children[0]._children[2]._on.click();   // Dismiss
        await flush();
        expect(dismissed).toEqual(['https://b.pod/inbox/n1']);
        expect(list._children).toHaveLength(0);
    });

    it('shows an empty state when there are no invitations', async () => {
        const ui = createSolidChatUI({ model: fakeModel({ listInvitations: async () => [] }) });
        const list = mkEl();
        await ui.renderInvitations(list, () => {});
        expect(harvest(list).text.join(' ')).toMatch(/no pending invitations/i);
    });

    it('renders a hostile invite title as text, never markup', async () => {
        const ui = createSolidChatUI({
            model: fakeModel({ listInvitations: async () => [{ id: 'n', from: 'https://a.pod/#me', container: 'https://a.pod/x/', title: '<img src=x onerror=alert(1)>' }] }),
        });
        const list = mkEl();
        await ui.renderInvitations(list, () => {});
        const h = harvest(list);
        expect(h.text.join(' ')).toContain('<img src=x onerror=alert(1)>');
        expect(h.html.join(' ')).not.toContain('<img');
    });
});

describe('send / host / join delegate to the model', () => {
    it('send posts and optimistically appends our message', async () => {
        const posted = [];
        const ui = createSolidChatUI({
            model: fakeModel({ sendMessage: async (id, t) => { posted.push(t); return true; } }),
            getMyWebId: () => 'https://me.pod/#me',
        });
        const feed = mkEl();
        expect(await ui.send('https://a.pod/x/', 'hello', feed)).toBe(true);
        expect(posted).toEqual(['hello']);
        expect(feed._children).toHaveLength(1);   // optimistic echo
    });

    it('host and join forward to the model and toast', async () => {
        const toasts = [];
        const ui = createSolidChatUI({ model: fakeModel(), showToast: (m) => toasts.push(m) });
        expect(await ui.host('Team', ['https://bob.pod/#me'])).toBeTruthy();
        expect(await ui.join('https://a.pod/x/', 'Alice chat')).toBeTruthy();
        expect(toasts.join(' ')).toMatch(/created/i);
        expect(toasts.join(' ')).toMatch(/joined/i);
    });
});
