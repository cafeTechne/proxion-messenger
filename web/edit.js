// edit.js — in-place message editing: the inline edit input, commit/cancel,
// and applying a server-confirmed message_edited event.
//
// A factory. Reassignable host state (socket, activeView, clientDid) is read
// live via getters. messageMap is host-owned shared state injected by reference
// (the renderer and dispatch also touch it). editingMsgId is cluster-owned and
// lives in `state` — main.js's Escape-key handler reads edit.state.editingMsgId.
// Returned functions are destructured into same-named bindings in main.js.
import { getLocale } from './i18n.js';

export function createEdit({ getSocket, getActiveView, getClientDid, getMessageMap }) {
    const state = { editingMsgId: null };

    function startEdit(msgId) {
        const msgEl = document.getElementById(`msg-${msgId}`);
        if (!msgEl) return;
        const textEl = msgEl.querySelector(".msg-text");
        if (!textEl) return;
        const original = textEl.innerText;
        state.editingMsgId = msgId;
        const inp = document.createElement("input");
        inp.type = "text";
        inp.value = original;
        inp.style.cssText = "width:60%;padding:4px;border-radius:4px;border:1px solid #555;background:#0f172a;color:#f1f5f9;font-size:0.95em;";
        const confirmBtn = document.createElement("button");
        confirmBtn.innerText = "✓";
        confirmBtn.className = "edit-confirm-btn";
        confirmBtn.style.cssText = "background:transparent;border:none;cursor:pointer;font-size:1em;margin-left:4px;";
        confirmBtn.onclick = () => commitEdit(msgId, inp.value);
        inp.onkeydown = (e) => {
            if (e.key === "Enter") { e.preventDefault(); commitEdit(msgId, inp.value); }
            if (e.key === "Escape") { cancelEdit(msgId, original); }
        };
        textEl.replaceWith(inp);
        (inp.closest(".msg-body") || msgEl).appendChild(confirmBtn);
        inp.focus();
    }

    function commitEdit(msgId, newContent) {
        const socket = getSocket();
        const activeView = getActiveView();
        if (!socket || !activeView || !newContent.trim()) return;
        const isLocal = activeView.local || activeView.type === "local_room" || activeView.type === "local_dm";
        let payload;
        if (isLocal) {
            payload = { cmd: "edit_local_message", message_id: msgId, thread_id: activeView.id, content: newContent.trim(), from_webid: getClientDid() };
        } else {
            payload = { cmd: "edit_message", message_id: msgId, content: newContent.trim() };
            if (activeView.type === "dm") payload.cert_id = activeView.id;
            else payload.room_id = activeView.id;
        }
        socket.send(JSON.stringify(payload));
        state.editingMsgId = null;
        // Immediately restore UI — server will confirm via message_edited event
        const msgEl = document.getElementById(`msg-${msgId}`);
        if (msgEl) {
            const inp = msgEl.querySelector("input[type=text]");
            if (inp) {
                const span = document.createElement("span");
                span.className = "msg-text";
                span.innerText = newContent.trim();
                inp.replaceWith(span);
            }
            const btn = msgEl.querySelector(".edit-confirm-btn");
            if (btn) btn.remove();
        }
    }

    function cancelEdit(msgId, original) {
        const msgEl = document.getElementById(`msg-${msgId}`);
        if (!msgEl) return;
        const inp = msgEl.querySelector("input[type=text]");
        if (inp) {
            const span = document.createElement("span");
            span.className = "msg-text";
            span.innerText = original;
            inp.replaceWith(span);
        }
        const confirmBtn = msgEl.querySelector(".edit-confirm-btn");
        if (confirmBtn) confirmBtn.remove();
        state.editingMsgId = null;
    }

    function handleMessageEdited(event) {
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
            tag.style.cssText = "font-size:0.75em;color:#94a3b8;margin-left:4px;";
            textEl.after(tag);
        }
        const editedTime = event.edited_at ? new Date(event.edited_at).toLocaleTimeString(getLocale(), { hour: "2-digit", minute: "2-digit" }) : "";
        tag.innerText = editedTime ? `(edited ${editedTime})` : "(edited)";
        const messageMap = getMessageMap();
        if (messageMap[event.message_id]) {
            messageMap[event.message_id].content = event.new_content;
            messageMap[event.message_id].edited_at = event.edited_at;
        }
    }

    return { startEdit, commitEdit, cancelEdit, handleMessageEdited, state };
}
