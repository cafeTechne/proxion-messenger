// modals.js — the smaller standalone panels: forward-message picker, schedule
// picker toggle, room-integrations (webhooks) panel, and search-results render.
//
// A factory. Reassignable host state (socket, activeView) is read live via
// getters; sendCmd / showToast / openSearchResult are injected. escHtml is
// imported directly. _forwardingMsgId is cluster-owned and lives in `state`.
// The returned functions are destructured into same-named bindings in main.js.
import { t } from './i18n.js';
import { escHtml, formatTimestamp } from './util.js';
import { icon } from './icons.js';
import { inlineNotice } from './states.js';
import { showPromptModal, closeButtonHtml } from './dialogs.js';

// Rooms to offer in the "Forward to…" picker. Each sidebar room row carries a
// [data-room-id] control; the row itself (li[data-name]) holds the room's
// display name, so show that instead of the raw room id.
export function forwardTargets(doc) {
    const seen = new Set();
    const out = [];
    doc.querySelectorAll('[data-room-id]').forEach(el => {
        const id = el.dataset.roomId;
        if (!id || seen.has(id)) return;
        seen.add(id);
        const row = el.closest ? el.closest('li[data-name]') : null;
        const name = (row && row.getAttribute('data-name')) || id;
        out.push({ id, name });
    });
    return out;
}

// What the sidebar search box should do with the current text: close the
// results (empty), ask for more characters, explain that search is offline, or
// run the search.
export const SEARCH_MIN_CHARS = 3;
export function searchAction(raw, online) {
    const query = String(raw == null ? '' : raw).trim();
    if (!query) return { kind: 'close', query };
    if (query.length < SEARCH_MIN_CHARS) return { kind: 'short', query };
    if (!online) return { kind: 'offline', query };
    return { kind: 'search', query };
}

export function createModals({ getSocket, getActiveView, sendCmd, showToast, getMessageContent, openSearchResult }) {
    const state = { forwardingMsgId: null };

    function openForwardModal(msgId) {
        state.forwardingMsgId = msgId;
        const socket = getSocket();
        const modal = document.getElementById('forward-modal');
        const list = document.getElementById('forward-thread-list');
        if (!modal || !list) return;
        const threads = forwardTargets(document);
        if (!threads.length) { list.innerHTML = inlineNotice(t('modal.noRoomsToForward')); }
        else {
            list.innerHTML = '';
            threads.forEach(thr => {
                const item = document.createElement('button');
                item.type = 'button';
                item.className = 'forward-thread-item picker-item';
                item.textContent = thr.name;
                item.addEventListener('click', () => {
                    if (socket && state.forwardingMsgId) {
                        // Send the PLAINTEXT we rendered — the gateway only has
                        // ciphertext for E2E DMs and would forward garbage.
                        const _content = getMessageContent ? getMessageContent(state.forwardingMsgId) : '';
                        socket.send(JSON.stringify({
                            cmd: 'forward_message', message_id: state.forwardingMsgId,
                            target_thread_id: thr.id, content: _content || '',
                        }));
                    }
                    modal.style.display = 'none';
                });
                list.appendChild(item);
            });
        }
        modal.style.display = 'flex';
    }

    // -- Round 69: Schedule picker --
    function openSchedulePicker() {
        const p = document.getElementById('schedule-picker');
        if (p) p.style.display = (p.style.display === 'none' || !p.style.display) ? 'flex' : 'none';
    }

    // -- Round 70: Integrations panel --
    function openIntegrationsPanel() {
        const activeView = getActiveView();
        const socket = getSocket();
        if (!activeView || !socket) return;
        socket.send(JSON.stringify({ cmd: 'list_webhooks', thread_id: activeView.id }));
        const existing = document.getElementById('integrations-modal');
        if (existing) existing.remove();
        const modal = document.createElement('div');
        modal.id = 'integrations-modal';
        modal.className = 'modal';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'integrations-title');
        modal.setAttribute('data-dismissable', '');
        modal.style.display = 'flex';
        const box = document.createElement('div');
        box.className = 'modal__panel modal__panel--md';
        box.innerHTML =
            '<div class="modal__header"><h3 id="integrations-title" class="modal__title"></h3>' + closeButtonHtml() + '</div>' +
            '<div id="webhook-list-area" style="margin-bottom:12px;min-height:40px;"></div>' +
            '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
            '<button type="button" id="ci-incoming-btn" class="btn btn--slate"></button>' +
            '<button type="button" id="ci-outgoing-btn" class="btn btn--slate"></button>' +
            '</div>' +
            '<div class="btn-row modal__footer"><button type="button" id="ci-close-btn" data-modal-cancel class="btn btn--slate btn--lg"></button></div>';
        box.querySelector('#integrations-title').textContent = t('ui.roomIntegrations2');
        box.querySelector('#ci-incoming-btn').textContent = t('integrations.addIncoming');
        box.querySelector('#ci-outgoing-btn').textContent = t('integrations.addOutgoing');
        box.querySelector('#ci-close-btn').textContent = t('btn.close');
        modal.appendChild(box);
        document.body.appendChild(modal);
        box.querySelector('#ci-close-btn').addEventListener('click', () => modal.remove());
        box.querySelector('#ci-incoming-btn').addEventListener('click', async () => {
            const name = await showPromptModal(t('integrations.botNamePrompt'), {
                title: t('integrations.addIncoming'), confirmLabel: t('integrations.create'), value: 'Bot',
            });
            if (name === null) return;
            sendCmd('create_webhook', { thread_id: activeView.id, direction: 'incoming', bot_name: name.trim() || 'Bot' });
            modal.remove();
        });
        box.querySelector('#ci-outgoing-btn').addEventListener('click', async () => {
            const url = await showPromptModal(t('integrations.urlPrompt'), {
                title: t('integrations.addOutgoing'), confirmLabel: t('integrations.create'),
                type: 'url', placeholder: 'https://',
            });
            if (url === null) return;
            if (!url.trim().startsWith('https://')) { showToast(t('modal.mustBeHttps'), 'error'); return; }
            sendCmd('create_webhook', { thread_id: activeView.id, direction: 'outgoing', url: url.trim(), bot_name: 'Bot' });
            modal.remove();
        });
    }

    // -- Search --
    // Results open in their own panel in place of the feed. The feed is only
    // hidden, never cleared, so closing the panel puts the conversation back
    // exactly where it was.
    let _searchTimer = null;
    let _feedScrollTop = 0;

    function _searchInput() { return document.getElementById('search-input'); }

    function _setSearchHint(text) {
        const hint = document.getElementById('search-hint');
        if (!hint) return;
        // Stays in the DOM (empty when idle) so its live region announces.
        hint.textContent = text || '';
    }

    function isSearchOpen() {
        const panel = document.getElementById('search-panel');
        return !!(panel && !panel.hidden);
    }

    function _openSearchPanel() {
        let panel = document.getElementById('search-panel');
        const feed = document.getElementById('message-feed');
        if (!panel) {
            panel = document.createElement('section');
            panel.id = 'search-panel';
            panel.className = 'search-panel';
            panel.setAttribute('role', 'region');
            panel.setAttribute('aria-labelledby', 'search-panel-title');
            panel.innerHTML =
                '<div class="search-panel__header">' +
                '<h2 id="search-panel-title" class="search-panel__title"></h2>' +
                `<button type="button" class="search-panel__close" data-search-close>${icon('x-mark', { size: 18 })}</button>` +
                '</div>' +
                '<ul class="search-panel__results" data-search-results></ul>';
            const close = panel.querySelector('[data-search-close]');
            close.setAttribute('aria-label', t('search.close'));
            close.setAttribute('title', t('search.close'));
            close.addEventListener('click', () => closeSearch({ focusInput: true }));
            if (feed && feed.parentNode) feed.parentNode.insertBefore(panel, feed);
            else document.body.appendChild(panel);
        }
        if (feed && feed.style.display !== 'none') {
            _feedScrollTop = feed.scrollTop || 0;
            feed.style.display = 'none';
        }
        panel.hidden = false;
        return panel;
    }

    // Close the panel and bring the conversation back. Returns true when a
    // panel was actually open (so an Escape handler knows it did something).
    function closeSearch({ focusInput = false, clearInput = true } = {}) {
        clearTimeout(_searchTimer);
        _setSearchHint('');
        const input = _searchInput();
        if (clearInput && input) input.value = '';
        const panel = document.getElementById('search-panel');
        if (!panel || panel.hidden) return false;
        panel.hidden = true;
        const feed = document.getElementById('message-feed');
        if (feed && feed.style.display === 'none') {
            feed.style.display = '';
            feed.scrollTop = _feedScrollTop;
        }
        if (focusInput && input && input.focus) input.focus();
        return true;
    }

    // Debounced handler for the sidebar search box.
    function onSearchInput(raw) {
        clearTimeout(_searchTimer);
        const socket = getSocket();
        const online = !!(socket && socket.readyState === 1);
        const action = searchAction(raw, online);
        if (action.kind === 'close') { closeSearch(); return; }
        if (action.kind === 'short') { _setSearchHint(t('search.tooShort')); return; }
        if (action.kind === 'offline') { _setSearchHint(t('search.offline')); return; }
        _setSearchHint('');
        _searchTimer = setTimeout(() => {
            const s = getSocket();
            if (!s || s.readyState !== 1) { _setSearchHint(t('search.offline')); return; }
            s.send(JSON.stringify({ cmd: 'search', query: action.query }));
        }, 500);
    }

    function renderSearchResults(event) {
        const query = String((event && event.query) || '');
        // A late reply for a box the user has since cleared or changed.
        const input = _searchInput();
        if (input && typeof input.value === 'string' && input.value.trim() !== query) return;
        const panel = _openSearchPanel();
        const title = panel.querySelector('#search-panel-title');
        if (title) title.textContent = t('search.resultsFor', { query });
        const list = panel.querySelector('[data-search-results]');
        if (!list) return;
        list.innerHTML = '';
        const results = (event && event.results) || [];
        if (results.length === 0) {
            list.innerHTML = `<li class="search-panel__empty">${inlineNotice(t('search.noMatches', { query }))}</li>`;
            return;
        }
        results.forEach(res => {
            const li = document.createElement('li');
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'search-result';
            const who = res.from_display_name || String(res.from_webid || '').slice(0, 16);
            const where = res.thread_name || '';
            const when = res.timestamp ? formatTimestamp(res.timestamp) : '';
            const meta = [who, where, when].filter(Boolean).map(escHtml).join(' · ');
            const text = String(res.content || '');
            btn.innerHTML =
                `<span class="search-result__meta">${meta}</span>` +
                `<span class="search-result__text">${escHtml(text.length > 200 ? text.slice(0, 200) + '…' : text)}</span>`;
            btn.addEventListener('click', () => {
                closeSearch();
                if (openSearchResult) openSearchResult(res.thread_id, res.message_id);
            });
            li.appendChild(btn);
            list.appendChild(li);
        });
    }

    return {
        openForwardModal, openSchedulePicker, openIntegrationsPanel, renderSearchResults,
        onSearchInput, closeSearch, isSearchOpen, state,
    };
}
