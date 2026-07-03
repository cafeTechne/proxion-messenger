// reactions.js — emoji reactions and the emoji picker.
//
// A factory. Reassignable host state (socket, activeView, selfWebId) is read
// live via getters. messageReactions is host-owned shared state (the message
// loader in main.js also populates it) so it is injected by reference through
// getMessageReactions() and mutated in place — it is never reassigned wholesale.
// lastEmojiMsgId is owned entirely by this cluster, so it lives in `state`.
// The returned functions are destructured into same-named bindings in main.js.
import { podWriteReactions } from './pod.js';

export function createReactions({ getSocket, getActiveView, getSelfWebId, getMessageReactions }) {
    const state = { lastEmojiMsgId: null };

    function handleReactionEvent(event, action) {
        const { message_id, emoji, from_webid } = event;
        const messageReactions = getMessageReactions();
        if (!messageReactions[message_id]) messageReactions[message_id] = {};
        if (!messageReactions[message_id][emoji]) messageReactions[message_id][emoji] = [];

        if (action === "add") {
            if (!messageReactions[message_id][emoji].includes(from_webid)) {
                messageReactions[message_id][emoji].push(from_webid);
            }
        } else {
            messageReactions[message_id][emoji] = messageReactions[message_id][emoji].filter(w => w !== from_webid);
        }
        // D3: animate the pill that was just added (not on initial message render).
        renderReactions(message_id, action === "add" ? emoji : null);
        // Pod: persist reaction state (only the acting user writes to their own pod)
        const activeView = getActiveView();
        if (activeView && activeView.type === 'local_room') {
            podWriteReactions(activeView.id, message_id, messageReactions[message_id] || {}).catch(() => {});
        }
    }

    function renderReactions(mid, animateEmoji) {
        const container = document.getElementById(`reactions-${mid}`);
        if (!container) return;
        const messageReactions = getMessageReactions();
        const selfWebId = getSelfWebId();
        const reacts = messageReactions[mid] || {};
        container.innerHTML = "";
        Object.keys(reacts).forEach(emoji => {
            const count = reacts[emoji].length;
            if (count === 0) return;
            const alreadyReacted = selfWebId && reacts[emoji].includes(selfWebId);
            const span = document.createElement("span");
            span.className = "reaction" + (alreadyReacted ? " active" : "") +
                (emoji === animateEmoji ? " reaction-anim" : "");
            span.innerText = `${emoji} ${count}`;
            span.onclick = () => alreadyReacted ? removeReaction(emoji, mid) : addEmoji(emoji, mid);
            container.appendChild(span);
        });
    }

    function togglePicker(msgId, x, y) {
        const picker = document.getElementById("emoji-picker");
        if (state.lastEmojiMsgId === msgId && picker.style.display === "grid") {
            picker.style.display = "none";
            return;
        }
        state.lastEmojiMsgId = msgId;
        picker.style.display = "grid";
        // Clamp to viewport so picker never clips off-screen on mobile
        const pw = picker.offsetWidth || 160;
        const ph = picker.offsetHeight || 120;
        const clampedLeft = Math.max(8, Math.min(x, window.innerWidth - pw - 8));
        const clampedTop = Math.max(8, y - ph - 8);
        picker.style.left = `${clampedLeft}px`;
        picker.style.top = `${clampedTop}px`;
    }

    function addEmoji(emoji, msgId = null) {
        const mid = msgId || state.lastEmojiMsgId;
        const picker = document.getElementById("emoji-picker");
        picker.style.display = "none";

        const activeView = getActiveView();
        // Guard like removeReaction — the thread may have closed while the picker
        // was open; without this activeView.type below throws.
        if (!getSocket() || !activeView || !mid) return;
        const payload = {
            cmd: "add_reaction",
            message_id: mid,
            emoji: emoji
        };
        if (activeView.type === "dm" || activeView.type === "local_dm") payload.cert_id = activeView.id;
        else payload.room_id = activeView.id;

        getSocket().send(JSON.stringify(payload));
    }

    function removeReaction(emoji, msgId) {
        const socket = getSocket();
        const activeView = getActiveView();
        if (!socket || !activeView) return;
        const payload = { cmd: "remove_reaction", message_id: msgId, emoji: emoji };
        if (activeView.type === "dm" || activeView.type === "local_dm")
            payload.cert_id = activeView.id;
        else
            payload.room_id = activeView.id;
        socket.send(JSON.stringify(payload));
    }

    return { handleReactionEvent, renderReactions, togglePicker, addEmoji, removeReaction, state };
}
