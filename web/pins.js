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
    revealMessage = (id) => document.getElementById(`msg-${id}`), send = null,
    readPins = null, savePin = null, resolvePin = null, watchPins = null,
    canRead = () => true, canManage = () => true, getSessionId = () => '',
    onUnavailable = () => {}, onSaveFailed = () => {}, onSaved = () => {},
    pollMs = 30000, setTimer = setInterval, clearTimer = clearInterval }) {
    let context = null, generation = 0, busy = false, again = false, timer = null, unwatch = null;
    let shownPins = new Map();
    const saves = new Map();
    const sameView = view => getActiveView()?.id === view.id && getActiveView()?.type === view.type;
    function current() {
        return context && sameView(context.view) && context.session === getSessionId()
            && document.getElementById('pin-panel').style.display !== 'none';
    }
    function closePinPanel() {
        generation++; context = null; busy = false; again = false; shownPins = new Map();
        if (unwatch) { try { unwatch(); } catch { /* cleanup continues */ } unwatch = null; }
        if (timer !== null) { clearTimer(timer); timer = null; }
        document.getElementById('pin-panel').style.display = 'none';
    }
    function loadFailed() {
        const list = document.getElementById('pin-list');
        list.innerHTML = inlineNotice(t('pin.loadFailed'));
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'btn'; retry.textContent = t('btn.retry');
        retry.onclick = refresh;
        list.appendChild(retry);
    }
    async function load(pending) {
        const gen = generation;
        busy = true;
        try {
            const result = await pending;
            if (gen !== generation || !current()) return;
            if (result?.ok) renderPins(result.pins);
            else { shownPins = new Map(); loadFailed(); }
        } catch { if (gen === generation && current()) loadFailed(); }
        finally {
            if (gen === generation) {
                busy = false;
                if (again) { again = false; refresh(); }
            }
        }
    }
    function refresh() {
        if (!current()) { closePinPanel(); return; }
        if (busy) { again = true; return; }
        return load(readPins(context.view));
    }
    function persist(view, id, add, pin) {
        if (!savePin) return null;
        const session = getSessionId(), key = JSON.stringify([session, view.type, view.id, id, add]);
        if (saves.has(key)) return saves.get(key);
        const pending = savePin(view, id, add, pin);
        if (!pending) return null;
        const task = Promise.resolve(pending).catch(() => ({ ok: false })).then(result => {
            if (session !== getSessionId() || !sameView(view)) return result;
            if (result?.ok) { onSaved(add); if (current()) refresh(); }
            else { onSaveFailed(result); if (result?.reason === 'conflict' && current()) refresh(); }
            return result;
        }).finally(() => { if (saves.get(key) === task) saves.delete(key); });
        saves.set(key, task);
        return task;
    }

    function _send(socket, payload) {
        if (send) send(payload);
        else socket.send(JSON.stringify(payload));
    }

    function pinMsg(msgId) {
        const socket = getSocket();
        const activeView = getActiveView();
        if (!activeView) return;
        if (!canManage(activeView)) { onUnavailable(); return; }
        const saved = persist({ ...activeView }, msgId, true, null);
        if (saved) return saved;
        if (!socket && !send) return;
        const threadId = (activeView.type === "dm" || activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id;
        _send(socket, { cmd: "pin_message", message_id: msgId, thread_id: threadId });
    }

    function showPinPanel() {
        const socket = getSocket(), view = getActiveView();
        if (!view) return;
        if (!canRead(view)) { onUnavailable(); return; }
        if (current()) return refresh();
        const pending = readPins?.({ ...view });
        if (pending) {
            closePinPanel();
            context = { view: { ...view }, session: getSessionId() };
            document.getElementById('pin-panel').style.display = 'block';
            document.getElementById('pin-list').innerHTML = inlineNotice(t('pin.loading'));
            try { unwatch = watchPins?.(context.view, refresh) || null; } catch { /* backstop below */ }
            timer = setTimer(refresh, pollMs); timer?.unref?.();
            return load(pending);
        }
        if (!socket) return;
        const threadId = (view.type === 'dm' || view.type === 'local_dm' ? 'dm:' : 'room:') + view.id;
        socket.send(JSON.stringify({ cmd: 'get_pins', thread_id: threadId }));
        document.getElementById('pin-panel').style.display = 'block';
    }

    function renderPins(pins) {
        const list = document.getElementById("pin-list");
        shownPins = new Map((pins || []).map(pin => [pin.message_id, pin]));
        list.innerHTML = "";
        if (!pins || pins.length === 0) {
            list.innerHTML = inlineNotice(t('pin.noneP')) + inlineNotice(t('pin.noneHint'));
            return;
        }
        const activeView = getActiveView();
        const threadId = activeView
            ? ((activeView.type === "local_dm" || activeView.type === "dm") ? "dm:" : "room:") + activeView.id
            : "";
        pins.forEach(pin => {
            const div = document.createElement("div");
            div.className = "ref-row";
            // Pins only record who pinned, so the author comes from the loaded
            // message; without it the row shows the text alone.
            const orig = pin.message || getMessages().find(m => m.message_id === pin.message_id);
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
                    ${canManage(activeView) ? `<button data-pin-action="unpin" data-msg-id="${msgIdEsc}" data-thread-id="${escHtml(threadId)}"
                        class="link-btn">${escHtml(t('pin.unpin'))}</button>` : ''}
                </div>`;
            list.appendChild(div);
        });
    }

    function unpinMsg(msgId, threadId) {
        const view = getActiveView();
        if (!view) return;
        if (!canManage(view)) { onUnavailable(); return; }
        const saved = persist({ ...view }, msgId, false, shownPins.get(msgId));
        if (saved) return saved;
        const socket = getSocket();
        if (!socket && !send) return;
        _send(socket, { cmd: "unpin_message", message_id: msgId, thread_id: threadId });
    }

    function jumpToMsg(msgId) {
        const view = getActiveView(), pin = shownPins.get(msgId), session = getSessionId();
        const pending = view && pin && resolvePin?.({ ...view }, pin);
        if (pending) return Promise.resolve(pending).then(element => {
            if (element && session === getSessionId() && sameView(view)) jumpToMessage(element, scrollBehavior());
        }).catch(() => { if (session === getSessionId() && sameView(view)) onSaveFailed({ reason: 'unavailable' }); });
        jumpToMessage(revealMessage(msgId), scrollBehavior());
    }
    function messageChanged(roomId, messageId) {
        if (current() && context.view.id === roomId && shownPins.has(messageId)) refresh();
    }

    return { pinMsg, showPinPanel, renderPins, unpinMsg, jumpToMsg, closePinPanel, refresh, messageChanged };
}
