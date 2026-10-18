// pins.js — pinned messages: pin/unpin commands and the pin panel.
//
// A factory with no cluster-owned state. Reassignable host state (socket,
// activeView) is read live via getters. The returned functions are destructured
// into same-named bindings in main.js so the dispatch and listener wiring keep
// working unchanged.
import { inlineNotice } from './states.js';
import { t } from './i18n.js';
import { escHtml, scrollBehavior, didIdTag } from './util.js';
import { jumpToMessage } from './wayfinding.js';
import { isUndecryptable } from './e2e-secondary.js';

// send (optional): queues pin/unpin while offline (connection.js
// socketSendOrQueue) instead of dropping them into a closed socket.
export function createPins({ getSocket, getActiveView, getMessages = () => [],
    revealMessage = (id) => document.getElementById(`msg-${id}`), send = null }) {
    function _send(socket, payload) {
        if (send) send(payload);
        else socket.send(JSON.stringify(payload));
    }

    function pinMsg(msgId) {
        const socket = getSocket();
        const activeView = getActiveView();
        if ((!socket && !send) || !activeView) return;
        const threadId = (activeView.type === "dm" || activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id;
        _send(socket, { cmd: "pin_message", message_id: msgId, thread_id: threadId });
    }

    function showPinPanel() {
        const socket = getSocket();
        const activeView = getActiveView();
        if (!socket || !activeView) return;
        const threadId = (activeView.type === "dm" || activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id;
        socket.send(JSON.stringify({ cmd: "get_pins", thread_id: threadId }));
        document.getElementById("pin-panel").style.display = "block";
    }

    function renderPins(pins) {
        const list = document.getElementById("pin-list");
        list.innerHTML = "";
        if (!pins || pins.length === 0) {
            list.innerHTML = inlineNotice(t('pin.noneP')) + inlineNotice(t('pin.noneHint'));
            return;
        }
        const activeView = getActiveView();
        const threadId = activeView
            ? (activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id
            : "";
        pins.forEach(pin => {
            const div = document.createElement("div");
            div.className = "ref-row";
            // Pins only record who pinned, so the author comes from the loaded
            // message; without it the row shows the text alone.
            const orig = getMessages().find(m => m.message_id === pin.message_id);
            const author = orig
                ? orig.from_display_name || (orig.from_webid ? "·" + didIdTag(orig.from_webid) : "")
                : "";
            // A sealed DM pin arrives as the placeholder when this device has
            // no plaintext for it (main.js resolves it through dmtext.js).
            const preview = isUndecryptable(pin.content)
                ? `<span class="msg-undecryptable">${escHtml(t('e2e.undecryptable'))}</span>`
                : escHtml((pin.content || "").slice(0, 80));
            // message_id is client-supplied and stored verbatim, so escape it before
            // it lands in these innerHTML attributes (stored XSS otherwise).
            const msgIdEsc = escHtml(pin.message_id);
            div.innerHTML = `${author ? `<div class="ref-row__meta">${escHtml(author)}</div>` : ""}
                <div class="ref-row__body">${preview}</div>
                <div class="ref-row__actions">
                    <button data-pin-action="jump" data-msg-id="${msgIdEsc}"
                        class="link-btn link-btn--info">${escHtml(t('pin.jump'))}</button>
                    <button data-pin-action="unpin" data-msg-id="${msgIdEsc}" data-thread-id="${threadId}"
                        class="link-btn">${escHtml(t('pin.unpin'))}</button>
                </div>`;
            list.appendChild(div);
        });
    }

    function unpinMsg(msgId, threadId) {
        const socket = getSocket();
        if (!socket && !send) return;
        _send(socket, { cmd: "unpin_message", message_id: msgId, thread_id: threadId });
    }

    function jumpToMsg(msgId) {
        // A pinned message paged out of the feed is rendered again first.
        jumpToMessage(revealMessage(msgId), scrollBehavior());
    }

    return { pinMsg, showPinPanel, renderPins, unpinMsg, jumpToMsg };
}
