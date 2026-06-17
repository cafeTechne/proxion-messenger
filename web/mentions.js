// mentions.js — the @-mention autocomplete for the message input: the dropdown
// render, keyboard navigation, and selection.
//
// A factory that owns the whole feature, including its input/keydown listeners
// (they share the _mentionStart / _mentionFocusIdx cursor state with the render
// and select functions, so bundling them keeps that state private). Call
// attach(inputEl) once to wire the listeners. currentRoomMembers is host-owned
// and read live via getCurrentRoomMembers(); webidColor is imported.
// closeMentionDropdown and _selectMention are also returned for the external
// callers in main.js (thread switches and the dropdown click handler).
import { webidColor } from './util.js';

export function createMentions({ getCurrentRoomMembers }) {
    const state = { mentionStart: -1, mentionFocusIdx: 0 };
    let inputEl = null;

    function _renderMentionDropdown(matches) {
        const dd = document.getElementById("mention-dropdown");
        if (!dd) return;
        state.mentionFocusIdx = 0;
        dd.innerHTML = "";
        matches.forEach((m, i) => {
            const name = m.display_name || (m.webid || "").slice(0, 12);
            const color = webidColor(m.webid);
            const initial = (name[0] || "?").toUpperCase();
            const row = document.createElement("div");
            row.className = "mention-option" + (i === 0 ? " focused" : "");
            row.dataset.idx = String(i);
            row.dataset.name = name;

            const avatar = document.createElement("div");
            avatar.className = "mo-avatar";
            avatar.style.background = color;
            avatar.textContent = initial;
            row.appendChild(avatar);

            const label = document.createElement("span");
            label.textContent = name;
            row.appendChild(label);

            if (m.status === "online") {
                const dot = document.createElement("span");
                dot.style.width = "6px";
                dot.style.height = "6px";
                dot.style.borderRadius = "50%";
                dot.style.background = "#22c55e";
                dot.style.display = "inline-block";
                dot.style.marginLeft = "auto";
                dot.style.flexShrink = "0";
                row.appendChild(dot);
            }
            dd.appendChild(row);
        });
        dd.style.display = "block";
    }

    function _selectMention(name) {
        const val = inputEl.value;
        const caret = inputEl.selectionStart;
        const before = val.slice(0, state.mentionStart);
        const after  = val.slice(caret);
        inputEl.value = `${before}@${name} ${after}`;
        const newPos = state.mentionStart + name.length + 2;
        inputEl.setSelectionRange(newPos, newPos);
        closeMentionDropdown();
        inputEl.focus();
    }

    function closeMentionDropdown() {
        const dd = document.getElementById("mention-dropdown");
        if (dd) dd.style.display = "none";
        state.mentionStart = -1;
    }

    function attach(el) {
        inputEl = el;

        el.addEventListener("input", () => {
            const val = inputEl.value;
            const caret = inputEl.selectionStart;
            let atPos = -1;
            for (let i = caret - 1; i >= 0; i--) {
                if (val[i] === "@" && (i === 0 || /\s/.test(val[i - 1]))) { atPos = i; break; }
                if (/\s/.test(val[i])) break;
            }
            const members = getCurrentRoomMembers();
            if (atPos === -1 || !members.length) { closeMentionDropdown(); return; }
            const query = val.slice(atPos + 1, caret).toLowerCase();
            const matches = members.filter(m =>
                (m.display_name || "").toLowerCase().includes(query) ||
                (m.webid || "").toLowerCase().includes(query)
            ).slice(0, 8);
            if (!matches.length) { closeMentionDropdown(); return; }
            state.mentionStart = atPos;
            _renderMentionDropdown(matches);
        });

        el.addEventListener("keydown", e => {
            const dd = document.getElementById("mention-dropdown");
            if (!dd || dd.style.display === "none") return;
            const items = dd.querySelectorAll(".mention-option");
            if (!items.length) return;
            if (e.key === "ArrowDown") {
                e.preventDefault();
                state.mentionFocusIdx = Math.min(state.mentionFocusIdx + 1, items.length - 1);
                items.forEach((el2, i) => el2.classList.toggle("focused", i === state.mentionFocusIdx));
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                state.mentionFocusIdx = Math.max(state.mentionFocusIdx - 1, 0);
                items.forEach((el2, i) => el2.classList.toggle("focused", i === state.mentionFocusIdx));
            } else if (e.key === "Enter" || e.key === "Tab") {
                const name = items[state.mentionFocusIdx]?.dataset.name;
                if (name) { e.preventDefault(); _selectMention(name); }
            } else if (e.key === "Escape") {
                e.preventDefault(); closeMentionDropdown();
            }
        });
    }

    return { attach, closeMentionDropdown, _selectMention, _renderMentionDropdown, state };
}
