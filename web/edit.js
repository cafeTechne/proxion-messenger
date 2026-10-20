// edit.js — in-place message editing: the inline edit input, commit/cancel,
// and applying a server-confirmed message_edited event.
//
// A factory. Reassignable host state (socket, activeView, clientDid) is read
// live via getters. messageMap is host-owned shared state injected by reference
// (the renderer and dispatch also touch it). editingMsgId is cluster-owned and
// lives in `state` — main.js's Escape-key handler reads edit.state.editingMsgId.
// Returned functions are destructured into same-named bindings in main.js.
import { getLocale, t } from './i18n.js';
import { supportsFieldSizing } from './util.js';

// sealEdit(view, messageId, text) (optional): for a DM with a keyed peer,
// returns the E2E-sealed fields that replace the plaintext `content`; null when
// the view needs no sealing; throws when sealing failed, and then nothing is
// sent and onSealFailed() is called.
// send (optional): queues the command while offline (connection.js
// socketSendOrQueue) instead of dropping it into a closed socket.
export function createEdit({ getSocket, getActiveView, getClientDid, getMessageMap, sealEdit, onSealFailed, send = null }) {
    const state = { editingMsgId: null };

    // Grow the edit box with its content, up to about 40% of the viewport.
    // Where CSS field-sizing: content is supported, style.css does it.
    function _autoGrow(ta) {
        if (supportsFieldSizing()) return;
        ta.style.height = "auto";
        const cap = typeof window !== "undefined" && window.innerHeight ? Math.round(window.innerHeight * 0.4) : 320;
        ta.style.height = Math.min(ta.scrollHeight || 0, cap) + "px";
    }

    // When the editor that is going away has focus, hand focus back to the
    // composer (the usual place to continue after ArrowUp, edit, Enter).
    function _refocusComposerIf(inp) {
        if (typeof document.activeElement === "undefined" || document.activeElement !== inp) return;
        const c = document.getElementById("message-input");
        if (c && typeof c.focus === "function") c.focus();
    }

    function startEdit(msgId) {
        const msgEl = document.getElementById(`msg-${msgId}`);
        if (!msgEl) return;
        if (state.editingMsgId && state.editingMsgId !== msgId) {
            cancelEdit(state.editingMsgId);
        }
        const textEl = msgEl.querySelector(".msg-text");
        if (!textEl) return;
        const original = textEl.innerText;
        state.editingMsgId = msgId;
        // Multi-line editor: Enter saves, Shift+Enter adds a newline, Escape
        // cancels. Enter that confirms an IME candidate is left to the IME.
        const inp = document.createElement("textarea");
        inp.className = "edit-input";
        inp.rows = 1;
        inp.value = original;
        inp.setAttribute("dir", "auto");
        inp.setAttribute("aria-label", t('msg.editLabel'));
        // Visible Save and Cancel under the editor, plus the key hint: the keys
        // were the only way out before, and a touch keyboard has no Escape.
        const actions = document.createElement("div");
        actions.className = "edit-actions";
        const hint = document.createElement("span");
        hint.className = "edit-hint";
        hint.textContent = t('msg.editHint');
        const cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "edit-cancel-btn";
        cancelBtn.textContent = t('btn.cancel');
        cancelBtn.onclick = () => cancelEdit(msgId);
        const confirmBtn = document.createElement("button");
        confirmBtn.type = "button";
        confirmBtn.className = "edit-confirm-btn";
        confirmBtn.textContent = t('btn.save');
        confirmBtn.setAttribute("aria-label", t('msg.saveEdit'));
        confirmBtn.onclick = () => commitEdit(msgId, inp.value);
        actions.appendChild(hint);
        actions.appendChild(cancelBtn);
        actions.appendChild(confirmBtn);
        inp.onkeydown = (e) => {
            // Keys typed here belong to the editor: the feed's list navigation
            // (arrows, Space, Enter open the message menu) must not see them.
            if (typeof e.stopPropagation === "function") e.stopPropagation();
            if (e.isComposing || e.keyCode === 229) return;
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                commitEdit(msgId, inp.value);
            } else if (e.key === "Escape") {
                // Handled here (propagation is stopped above), so the
                // document-level Escape (dialogs, reply bar) does not also fire.
                e.preventDefault();
                cancelEdit(msgId);
            }
        };
        inp.oninput = () => _autoGrow(inp);
        state._origTextEl = textEl;
        state._original = original;
        textEl.replaceWith(inp);
        inp.after(actions);
        _autoGrow(inp);
        inp.focus();
        if (typeof inp.setSelectionRange === "function") inp.setSelectionRange(inp.value.length, inp.value.length);
    }

    function commitEdit(msgId, newContent) {
        const socket = getSocket();
        const activeView = getActiveView();
        if ((!socket && !send) || !activeView || !newContent.trim()) return;
        const _send = send || ((p) => socket.send(JSON.stringify(p)));
        const original = state._original;
        const isLocal = activeView.local || activeView.type === "local_room" || activeView.type === "local_dm";
        let payload;
        if (isLocal) {
            payload = { cmd: "edit_local_message", message_id: msgId, thread_id: activeView.id, content: newContent.trim(), from_webid: getClientDid() };
        } else {
            payload = { cmd: "edit_message", message_id: msgId, content: newContent.trim() };
            if (activeView.type === "dm") payload.cert_id = activeView.id;
            else payload.room_id = activeView.id;
        }
        const isDm = activeView.type === "dm" || activeView.type === "local_dm";
        if (isDm && sealEdit) {
            // Sealed send is async; a failure puts the old text back.
            Promise.resolve().then(() => sealEdit(activeView, msgId, payload.content))
                .then((sealed) => { _send(sealed ? { ...payload, ...sealed } : payload); })
                .catch(() => {
                    const el = document.getElementById(`msg-${msgId}`)?.querySelector(".msg-text");
                    if (el && original != null) el.innerText = original;
                    if (onSealFailed) onSealFailed();
                });
        } else {
            _send(payload);
        }
        state.editingMsgId = null;
        // Immediately restore UI — server will confirm via message_edited event
        const msgEl = document.getElementById(`msg-${msgId}`);
        if (msgEl) {
            const inp = msgEl.querySelector(".edit-input");
            if (inp) {
                _refocusComposerIf(inp);
                const span = document.createElement("span");
                span.className = "msg-text";
                span.setAttribute("dir", "auto");
                span.innerText = newContent.trim();
                inp.replaceWith(span);
            }
            msgEl.querySelector(".edit-actions")?.remove();
        }
        state._origTextEl = null;
    }

    // Put the message text back as it was. `original` is only used when the
    // rendered element was not kept (it normally is, markdown intact).
    function cancelEdit(msgId, original) {
        const msgEl = document.getElementById(`msg-${msgId}`);
        if (msgEl) {
            const inp = msgEl.querySelector(".edit-input");
            if (inp) {
                _refocusComposerIf(inp);
                let restore = state._origTextEl;
                if (!restore) {
                    restore = document.createElement("span");
                    restore.className = "msg-text";
                    restore.innerText = original ?? state._original ?? "";
                }
                inp.replaceWith(restore);
            }
            msgEl.querySelector(".edit-actions")?.remove();
        }
        state._origTextEl = null;
        state.editingMsgId = null;
    }

    function handleMessageEdited(event) {
        // The buffered copy first: a message paged out of the feed is drawn
        // from it again when the reader scrolls back.
        const messageMap = getMessageMap();
        if (messageMap[event.message_id]) {
            messageMap[event.message_id].content = event.new_content;
            messageMap[event.message_id].edited_at = event.edited_at;
        }
        const msgEl = document.getElementById(`msg-${event.message_id}`);
        if (!msgEl) return;
        let textEl = msgEl.querySelector(".msg-text");
        if (!textEl) {
            textEl = document.createElement("span");
            textEl.className = "msg-text";
            msgEl.appendChild(textEl);
        }
        textEl.innerText = event.new_content;
        let tag = msgEl.querySelector(".edited-tag");
        if (!tag) {
            tag = document.createElement("span");
            tag.className = "edited-tag";
            textEl.after(tag);
        }
        const editedTime = event.edited_at ? new Date(event.edited_at).toLocaleTimeString(getLocale(), { hour: "2-digit", minute: "2-digit" }) : "";
        tag.innerText = editedTime ? t('msg.editedAt', { time: editedTime }) : t('msg.edited');
    }

    return { startEdit, commitEdit, cancelEdit, handleMessageEdited, state };
}
