// reactions.js — emoji reactions and the emoji picker.
//
// A factory. Reassignable host state (socket, activeView, selfWebId) is read
// live via getters. messageReactions is host-owned shared state (the message
// loader in main.js also populates it) so it is injected by reference through
// getMessageReactions() and mutated in place — it is never reassigned wholesale.
// lastEmojiMsgId is owned entirely by this cluster, so it lives in `state`.
// The returned functions are destructured into same-named bindings in main.js.
import { podWriteReactions } from './pod.js';

export function createReactions({ getSocket, getActiveView, getSelfWebId, getMessageReactions, getRoomEmojiMap }) {
    const state = { lastEmojiMsgId: null };

    // R60A: a reaction key like ":name:" refers to the room's custom emoji.
    // Returns the map entry or null. Safe by construction: the key must match
    // the strict name charset AND exist in the server-validated room map.
    function _customEntryFor(key) {
        const m = /^:([a-z0-9_]{2,32}):$/.exec(key || '');
        if (!m) return null;
        const map = getRoomEmojiMap?.() || {};
        return map[m[1]] || null;
    }

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
            // R60A: ":name:" keys render the room's custom emoji image (safe:
            // createElement + server-validated map, no innerHTML). Members
            // whose map lacks the name see the literal key — still readable.
            const _custom = _customEntryFor(emoji);
            if (_custom) {
                const img = document.createElement("img");
                img.className = "custom-emoji";
                img.src = `data:${_custom.mime};base64,${_custom.data_b64}`;
                img.alt = emoji;
                span.appendChild(img);
                span.appendChild(document.createTextNode(` ${count}`));
            } else {
                span.innerText = `${emoji} ${count}`;
            }
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
        // R60A: rebuild the active room's custom emoji entries on each open.
        if (typeof picker.querySelectorAll === "function") {
            picker.querySelectorAll(".custom-entry").forEach(el => el.remove());
            const map = getRoomEmojiMap?.() || {};
            for (const name of Object.keys(map).sort().reverse()) {   // prepend keeps a→z
                const b = document.createElement("button");
                b.className = "custom-entry";
                b.setAttribute("role", "menuitem");
                b.setAttribute("aria-label", ":" + name + ":");
                const img = document.createElement("img");
                img.className = "custom-emoji";
                img.src = `data:${map[name].mime};base64,${map[name].data_b64}`;
                img.alt = "";
                b.appendChild(img);
                b.addEventListener("click", () => addEmoji(":" + name + ":", msgId));
                picker.prepend(b);
            }
        }
        picker.style.display = "grid";
        // Clamp to viewport so picker never clips off-screen on mobile
        const pw = picker.offsetWidth || 160;
        const ph = picker.offsetHeight || 120;
        const clampedLeft = Math.max(8, Math.min(x, window.innerWidth - pw - 8));
        const clampedTop = Math.max(8, y - ph - 8);
        picker.style.left = `${clampedLeft}px`;
        picker.style.top = `${clampedTop}px`;
        // Keyboard: remember the opener to restore focus to, move focus into the
        // grid, and wire arrow-key navigation + Escape once.
        state._emojiOpener = (typeof document !== "undefined" && document.activeElement) || null;
        if (!picker._kbdWired && typeof picker.addEventListener === "function") {
            picker._kbdWired = true;
            picker.addEventListener("keydown", (e) => {
                const btns = [...picker.querySelectorAll("button")];
                const i = btns.indexOf(document.activeElement);
                const COLS = 4;
                let next = -1;
                if (e.key === "ArrowRight") next = i + 1;
                else if (e.key === "ArrowLeft") next = i - 1;
                else if (e.key === "ArrowDown") next = i + COLS;
                else if (e.key === "ArrowUp") next = i - COLS;
                else if (e.key === "Home") next = 0;
                else if (e.key === "End") next = btns.length - 1;
                else if (e.key === "Escape") {
                    e.preventDefault();
                    picker.style.display = "none";
                    const opener = state._emojiOpener;
                    if (opener && document.contains(opener)) { try { opener.focus(); } catch { /* gone */ } }
                    return;
                } else return;
                if (next >= 0 && next < btns.length) { e.preventDefault(); btns[next].focus(); }
            });
        }
        if (typeof requestAnimationFrame !== "undefined" && typeof picker.querySelector === "function") {
            requestAnimationFrame(() => { picker.querySelector("button")?.focus(); });
        }
    }

    function addEmoji(emoji, msgId = null) {
        const mid = msgId || state.lastEmojiMsgId;
        const picker = document.getElementById("emoji-picker");
        const _pickerWasOpen = picker.style.display !== "none";
        picker.style.display = "none";
        // Return focus to the message the picker was opened from (keyboard flow).
        if (_pickerWasOpen && state._emojiOpener && document.contains(state._emojiOpener)) {
            try { state._emojiOpener.focus(); } catch { /* gone */ }
        }

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
