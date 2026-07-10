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

export function createModals({ getSocket, getActiveView, sendCmd, showToast, renderMessage, getMessageContent }) {
    const state = { forwardingMsgId: null };

    function openForwardModal(msgId) {
        state.forwardingMsgId = msgId;
        const socket = getSocket();
        const modal = document.getElementById('forward-modal');
        const list = document.getElementById('forward-thread-list');
        if (!modal || !list) return;
        const threads = [];
        document.querySelectorAll('[data-room-id]').forEach(el => {
            const name = el.querySelector('.room-name')?.textContent || el.dataset.roomId;
            threads.push({ id: el.dataset.roomId, name });
        });
        if (!threads.length) { list.innerHTML = inlineNotice(t('modal.noRoomsToForward')); }
        else {
            list.innerHTML = '';
            threads.forEach(thr => {
                const item = document.createElement('div');
                item.className = 'forward-thread-item';
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
        modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1003;display:flex;align-items:center;justify-content:center;';
        const box = document.createElement('div');
        box.style.cssText = 'background:#1e293b;border-radius:8px;padding:20px;min-width:340px;color:#f1f5f9;';
        box.innerHTML = '<h3 style="margin:0 0 12px">Room Integrations</h3>' +
            '<div id="webhook-list-area" style="margin-bottom:12px;min-height:40px;"></div>' +
            '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">' +
            '<button id="ci-incoming-btn" style="background:var(--accent,#e94560);border:none;color:#fff;padding:7px 14px;border-radius:4px;cursor:pointer">+ Incoming Webhook</button>' +
            '<button id="ci-outgoing-btn" style="background:#334155;border:none;color:#f1f5f9;padding:7px 14px;border-radius:4px;cursor:pointer">+ Outgoing Webhook</button>' +
            '</div><button id="ci-close-btn" style="background:#334155;border:none;color:#f1f5f9;padding:7px 14px;border-radius:4px;cursor:pointer">Close</button>';
        modal.appendChild(box);
        document.body.appendChild(modal);
        box.querySelector('#ci-close-btn').addEventListener('click', () => modal.remove());
        box.querySelector('#ci-incoming-btn').addEventListener('click', () => {
            const name = prompt('Bot display name:', 'Bot') || 'Bot';
            sendCmd('create_webhook', { thread_id: activeView.id, direction: 'incoming', bot_name: name });
            modal.remove();
        });
        box.querySelector('#ci-outgoing-btn').addEventListener('click', () => {
            const url = prompt('Target HTTPS URL:');
            if (!url || !url.startsWith('https://')) { showToast(t('modal.mustBeHttps'), 'error'); return; }
            sendCmd('create_webhook', { thread_id: activeView.id, direction: 'outgoing', url, bot_name: 'Bot' });
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
