// solidchat-ui.js — the Solid-conversation panel: a list of cross-app
// conversations and a thread view. Distinct from Proxion DMs/rooms and clearly
// labelled as plaintext, because these messages are shared, not E2E.
//
// SECURITY: message content and author names come from ARBITRARY other Solid
// apps and users. Everything user-supplied is rendered with textContent /
// createElement, never innerHTML with interpolation, so a hostile message
// cannot inject markup. This mirrors the b64attr/escaping discipline elsewhere.

// Well-known WebID container names that do not identify the user; skip them so
// the label lands on the username (CSS puts it first: /{user}/profile/card#me)
// or on the host for a root-hosted pod (/profile/card#me).
const _GENERIC_SEG = new Set(['profile', 'card', 'public', 'private', 'me']);

function shortWebId(webid) {
    const s = String(webid || '');
    try {
        const u = new URL(s);
        const seg = u.pathname.split('/').filter(Boolean).find(x => !_GENERIC_SEG.has(x.toLowerCase()));
        return seg ? `${seg}@${u.host}` : u.host;
    } catch {
        return s.slice(0, 24) || 'someone';
    }
}

export function createSolidChatUI({ model, getMyWebId = () => '', showToast = () => {} }) {
    let _unsub = null;
    let _openId = null;

    function _clearSub() {
        if (_unsub) { try { _unsub(); } catch { /* ignore */ } _unsub = null; }
    }

    /** Render the list of conversations into `listEl`; calls onOpen(id) on click. */
    function renderList(listEl, onOpen) {
        if (!listEl) return;
        listEl.innerHTML = '';
        const convs = model.listConversations();
        if (!convs.length) {
            // Must be an <li> — a <ul> may only contain <li> (WCAG list rule).
            const li = document.createElement('li');
            li.className = 'solidchat-empty';
            li.textContent = 'No Solid conversations yet. Host one or join with a link.';
            listEl.appendChild(li);
            return;
        }
        for (const c of convs) {
            const li = document.createElement('li');
            const item = document.createElement('button');
            item.className = 'solidchat-item';
            item.type = 'button';
            item.dataset.id = c.id;
            const title = document.createElement('span');
            title.className = 'solidchat-item-title';
            title.textContent = c.title || shortWebId(c.id);   // textContent: safe
            const role = document.createElement('span');
            role.className = 'solidchat-item-role';
            role.textContent = c.role === 'host' ? 'hosting' : 'joined';
            item.append(title, role);
            item.addEventListener('click', () => onOpen && onOpen(c.id));
            li.appendChild(item);
            listEl.appendChild(li);
        }
    }

    /** Render a message list into `feedEl`. Author + body via textContent. */
    function renderMessages(feedEl, msgs) {
        if (!feedEl) return;
        const me = getMyWebId();
        feedEl.innerHTML = '';
        for (const m of msgs || []) {
            const row = document.createElement('div');
            row.className = 'solidchat-msg' + (m.from_webid && m.from_webid === me ? ' mine' : '');
            const author = document.createElement('span');
            author.className = 'solidchat-author';
            author.textContent = m.from_display_name || shortWebId(m.from_webid);   // safe
            const body = document.createElement('span');
            body.className = 'solidchat-body';
            body.textContent = m.content || '';                                     // safe (plaintext, not markdown)
            row.append(author, body);
            feedEl.appendChild(row);
        }
    }

    /**
     * Open a conversation: load its history into `feedEl`, then subscribe for
     * live updates, appending new messages as they arrive. Returns when the
     * initial load is done.
     */
    async function openConversation(id, feedEl) {
        _clearSub();
        _openId = id;
        const initial = await model.loadConversation(id, 7);
        if (_openId !== id) return;          // switched away during the load
        renderMessages(feedEl, initial);
        _unsub = model.subscribeConversation(id, (fresh) => {
            if (_openId !== id) return;
            for (const m of fresh) {
                if (feedEl.querySelector(`[data-mid="${cssEscape(m.message_id)}"]`)) continue;
                appendMessage(feedEl, m);
            }
        }, { intervalMs: 5000, days: 1 });
    }

    function appendMessage(feedEl, m) {
        const me = getMyWebId();
        const row = document.createElement('div');
        row.className = 'solidchat-msg' + (m.from_webid === me ? ' mine' : '');
        row.dataset.mid = m.message_id || '';
        const author = document.createElement('span');
        author.className = 'solidchat-author';
        author.textContent = m.from_display_name || shortWebId(m.from_webid);
        const body = document.createElement('span');
        body.className = 'solidchat-body';
        body.textContent = m.content || '';
        row.append(author, body);
        feedEl.appendChild(row);
    }

    function cssEscape(s) {
        return String(s || '').replace(/["\\\]]/g, '\\$&');
    }

    async function send(id, text, feedEl) {
        const ok = await model.sendMessage(id, text);
        if (ok) {
            // Optimistically show our own message; the poll will dedup by id.
            appendMessage(feedEl, { message_id: '', from_webid: getMyWebId(), content: String(text).trim() });
        }
        return ok;
    }

    async function host(title, participantWebIds) {
        const conv = await model.hostConversation({ title, participantWebIds });
        if (conv) showToast('Conversation created. Share its link to invite others.');
        return conv;
    }

    async function join(url, title) {
        const conv = await model.joinConversation(url, { title });
        if (conv) showToast('Joined the conversation.');
        return conv;
    }

    function close() { _clearSub(); _openId = null; }

    return { renderList, renderMessages, openConversation, send, host, join, close, shortWebId };
}

export { shortWebId };
