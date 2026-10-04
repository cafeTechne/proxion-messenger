// modals.js — the smaller standalone panels: forward-message picker, schedule
// picker toggle, room-integrations (webhooks) panel, and search-results render.
//
// A factory. Reassignable host state (socket, activeView) is read live via
// getters; sendCmd / showToast / renderMessage are injected. escHtml is
// imported directly. _forwardingMsgId is cluster-owned and lives in `state`.
// The returned functions are destructured into same-named bindings in main.js.
import { t } from './i18n.js';
import { escHtml } from './util.js';
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

export function createModals({ getSocket, getActiveView, sendCmd, showToast, renderMessage, getMessageContent }) {
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

    function renderSearchResults(event) {
        const feed = document.getElementById("message-feed");
        feed.innerHTML = `<div class="system-msg">Search results for "${escHtml(event.query)}":</div>`;
        if (event.results.length === 0) {
            feed.innerHTML += '<div class="system-msg">No matches found.</div>';
        }
        event.results.forEach(res => {
            renderMessage({ ...res, is_search_result: true });
        });
    }

    return { openForwardModal, openSchedulePicker, openIntegrationsPanel, renderSearchResults, state };
}
