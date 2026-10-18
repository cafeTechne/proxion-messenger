// reactions.js — emoji reactions and the emoji picker.
//
// A factory. Reassignable host state (socket, activeView, selfWebId) is read
// live via getters. messageReactions is host-owned shared state (the message
// loader in main.js also populates it) so it is injected by reference through
// getMessageReactions() and mutated in place — it is never reassigned wholesale.
// lastEmojiMsgId is owned entirely by this cluster, so it lives in `state`.
// The returned functions are destructured into same-named bindings in main.js.
import { podWriteReactions, podWriteReactionAction, podWriteReactionActionAt } from './pod.js';
import { t, tn } from './i18n.js';
import { pollVoteChange } from './polls.js';
import { icon } from './icons.js';

// resolveName(webid) -> display name or "" (the same resolver the typing
// indicator uses); it names the reactors in each pill's tooltip.
//
// DM reactions to a keyed peer are E2E-sealed (e2e-secondary.js):
// sealReaction(view, messageId, emoji) returns the fields to send (an opaque
// token in `emoji` plus the sealed header), null when no sealing applies, or
// throws (nothing is sent, onSealFailed() is called). wireEmoji(messageId,
// emoji) names our own reaction for remove_reaction (its token, if sealed).
// send (optional): queues reactions while offline (connection.js
// socketSendOrQueue) so they go out on reconnect instead of being dropped.
export function createReactions({ getSocket, getActiveView, getSelfWebId, getMessageReactions, getRoomEmojiMap, getMessageTs, getRemoteRoom, resolveName, sealReaction, wireEmoji, onSealFailed, send = null }) {
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
            // R101: also mirror this reaction into the Long Chat day file as a
            // schema:LikeAction so other Solid apps see it. Needs the message's
            // timestamp to build its pod IRI; a no-op if we do not have it.
            const ts = getMessageTs?.(message_id);
            if (ts) {
                // A joined room lives on the owner's pod; write the LikeAction into
                // their container so participants actually see it (the self-pod
                // wrapper would mirror it into the reactor's own pod, unseen).
                const _rr = getRemoteRoom?.(activeView.id);
                (_rr ? podWriteReactionActionAt(_rr.container, message_id, ts, emoji, from_webid, action === 'add')
                     : podWriteReactionAction(activeView.id, message_id, ts, emoji, from_webid, action === 'add'))
                    .catch(() => {});
            }
        }
    }

    // Comma list of who reacted ("You, Alice, Bob"), at most 10 names.
    function reactorNames(webids) {
        const self = getSelfWebId();
        const names = (webids || []).map(w => (w && w === self) ? t('reaction.you')
            : ((resolveName && resolveName(w)) || String(w || "").slice(0, 16)));
        if (names.length > 10) {
            return t('reaction.namesMore', { names: names.slice(0, 10).join(", "), count: names.length - 10 });
        }
        return names.join(", ");
    }

    function renderReactions(mid, animateEmoji) {
        const container = document.getElementById(`reactions-${mid}`);
        if (!container) return;
        const messageReactions = getMessageReactions();
        const selfWebId = getSelfWebId();
        const reacts = messageReactions[mid] || {};
        container.innerHTML = "";
        // A poll's votes are shown on its own option buttons, not as pills.
        const pollKeys = _renderPollCounts(container, reacts, selfWebId);
        let shown = 0;
        Object.keys(reacts).forEach(emoji => {
            const count = reacts[emoji].length;
            if (count === 0 || pollKeys.has(emoji)) return;
            const alreadyReacted = !!(selfWebId && reacts[emoji].includes(selfWebId));
            const span = document.createElement("span");
            span.className = "reaction" + (alreadyReacted ? " active" : "") +
                (emoji === animateEmoji ? " reaction-anim" : "");
            // A11y: expose each pill as a toggle button whose aria-pressed reflects
            // whether the CURRENT user has reacted with this emoji, and make it
            // keyboard-focusable. (Guarded for the DOM stubs used in unit tests.)
            if (typeof span.setAttribute === "function") {
                span.setAttribute("role", "button");
                span.setAttribute("aria-pressed", alreadyReacted ? "true" : "false");
                const label = t('reaction.by', { emoji, names: reactorNames(reacts[emoji]) });
                span.setAttribute("aria-label", label);
                span.title = label;
            }
            span.tabIndex = 0;
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
            const toggle = () => alreadyReacted ? removeReaction(emoji, mid) : addEmoji(emoji, mid);
            span.onclick = toggle;
            // Keyboard: Enter / Space activate the pill like a button.
            if (typeof span.addEventListener === "function") {
                span.addEventListener("keydown", (e) => {
                    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
                        e.preventDefault();
                        toggle();
                    }
                });
            }
            container.appendChild(span);
            shown++;
        });
        // A small "add reaction" pill at the end of a non-empty row opens the
        // same picker as the hover bar's react button.
        if (shown > 0) {
            const add = document.createElement("button");
            add.className = "reaction reaction-add";
            if (typeof add.setAttribute === "function") {
                add.setAttribute("type", "button");
                add.setAttribute("aria-label", t('reaction.add'));
            }
            add.title = t('reaction.add');
            add.innerHTML = icon('face-smile', { size: 14 }) + '<span aria-hidden="true">+</span>';
            add.onclick = (e) => {
                if (e && typeof e.stopPropagation === "function") e.stopPropagation();
                const r = typeof add.getBoundingClientRect === "function" ? add.getBoundingClientRect() : { left: 0, top: 0 };
                togglePicker(mid, r.left, r.top);
            };
            container.appendChild(add);
        }
    }

    // Fills in the poll card next to `container` (if any): per-option count,
    // a proportional bar, aria-pressed for your own vote, and the total.
    // Returns the set of emoji keys that are poll options.
    function _renderPollCounts(container, reacts, selfWebId) {
        const card = typeof container.parentElement?.querySelector === "function"
            ? container.parentElement.querySelector(".poll-card") : null;
        if (!card) return new Set();
        const btns = [...card.querySelectorAll(".poll-opt[data-emoji]")];
        const counts = btns.map(b => (reacts[b.dataset.emoji] || []).length);
        const total = counts.reduce((a, b) => a + b, 0);
        btns.forEach((b, i) => {
            const voters = reacts[b.dataset.emoji] || [];
            b.setAttribute("aria-pressed", selfWebId && voters.includes(selfWebId) ? "true" : "false");
            b.querySelector(".poll-opt-count").textContent = counts[i] ? String(counts[i]) : "";
            b.querySelector(".poll-opt-bar").style.width = total ? `${Math.round(100 * counts[i] / total)}%` : "0";
            b.title = voters.length ? reactorNames(voters) : "";
        });
        const hint = card.querySelector(".poll-hint");
        if (hint) hint.textContent = total ? tn('poll.votes', total) : t('poll.voteHint');
        return new Set(btns.map(b => b.dataset.emoji));
    }

    // One vote per person: moves or clears your keycap reaction.
    function votePoll(msgId, emoji) {
        if (!msgId || !emoji) return;
        const reacts = getMessageReactions()[msgId] || {};
        const { add, remove } = pollVoteChange(reacts, emoji, getSelfWebId());
        remove.forEach(e => removeReaction(e, msgId));
        if (add) addEmoji(add, msgId);
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
        picker.style.right = "auto";
        picker.style.bottom = "auto";
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
        if (!activeView || !mid) return;
        const payload = {
            cmd: "add_reaction",
            message_id: mid,
            emoji: emoji
        };
        const isDm = activeView.type === "dm" || activeView.type === "local_dm";
        if (isDm) payload.cert_id = activeView.id;
        else payload.room_id = activeView.id;

        if (isDm && sealReaction) {
            return Promise.resolve().then(() => sealReaction(activeView, mid, emoji))
                .then((sealed) => { _safeSend(sealed ? { ...payload, ...sealed } : payload); })
                .catch(() => { if (onSealFailed) onSealFailed(); });
        }
        _safeSend(payload);
    }

    // With a queueing send, hand it over (it waits for the connection).
    // Otherwise only send on an OPEN socket: a raw .send() on a CONNECTING
    // socket throws InvalidStateError and on CLOSING/CLOSED it is discarded.
    function _safeSend(payload) {
        if (send) { send(payload); return true; }
        const s = getSocket();
        const OPEN = (typeof WebSocket !== 'undefined' && WebSocket.OPEN) || 1;
        if (!s || s.readyState !== OPEN) return false;
        s.send(JSON.stringify(payload));
        return true;
    }

    function removeReaction(emoji, msgId) {
        const activeView = getActiveView();
        if (!activeView) return;
        const payload = { cmd: "remove_reaction", message_id: msgId, emoji: emoji };
        if (activeView.type === "dm" || activeView.type === "local_dm") {
            payload.cert_id = activeView.id;
            if (wireEmoji) payload.emoji = wireEmoji(msgId, emoji);
        }
        else
            payload.room_id = activeView.id;
        _safeSend(payload);
    }

    return { handleReactionEvent, renderReactions, togglePicker, addEmoji, removeReaction, votePoll, state };
}
