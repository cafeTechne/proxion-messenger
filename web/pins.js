// pins.js — pinned messages: pin/unpin commands and the pin panel.
//
// A factory with no cluster-owned state. Reassignable host state (socket,
// activeView) is read live via getters. The returned functions are destructured
// into same-named bindings in main.js so the dispatch and listener wiring keep
// working unchanged.
import { inlineNotice } from './states.js';

export function createPins({ getSocket, getActiveView }) {

    function pinMsg(msgId) {
        const socket = getSocket();
        const activeView = getActiveView();
        if (!socket || !activeView) return;
        const threadId = (activeView.type === "dm" || activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id;
        socket.send(JSON.stringify({ cmd: "pin_message", message_id: msgId, thread_id: threadId }));
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
            list.innerHTML = inlineNotice("No pinned messages.");
            return;
        }
        const activeView = getActiveView();
        const threadId = activeView
            ? (activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id
            : "";
        pins.forEach(pin => {
            const div = document.createElement("div");
            div.style.cssText = "border-bottom:1px solid #334155;padding:8px 0;color:#f1f5f9;";
            const pinner = (pin.pinned_by || "").slice(0, 20) || "unknown";
            const preview = (pin.content || "").slice(0, 80);
            div.innerHTML = `<div style="font-size:0.85em;color:#94a3b8">${pinner}</div>
                <div style="margin:2px 0;">${preview.replace(/</g,"&lt;")}</div>
                <div style="display:flex;gap:8px;margin-top:4px;">
                    <button data-pin-action="jump" data-msg-id="${pin.message_id}"
                        style="background:transparent;border:none;color:#7dd3fc;cursor:pointer;padding:0;font-size:0.8em;">[Jump]</button>
                    <button data-pin-action="unpin" data-msg-id="${pin.message_id}" data-thread-id="${threadId}"
                        style="background:transparent;border:none;color:#94a3b8;cursor:pointer;padding:0;font-size:0.8em;">Unpin</button>
                </div>`;
            list.appendChild(div);
        });
    }

    function unpinMsg(msgId, threadId) {
        const socket = getSocket();
        if (!socket) return;
        socket.send(JSON.stringify({ cmd: "unpin_message", message_id: msgId, thread_id: threadId }));
    }

    function jumpToMsg(msgId) {
        const el = document.getElementById(`msg-${msgId}`);
        if (el) { el.scrollIntoView({ behavior: "smooth" }); el.style.background = "#334155"; setTimeout(() => el.style.background = "", 1500); }
    }

    return { pinMsg, showPinPanel, renderPins, unpinMsg, jumpToMsg };
}
