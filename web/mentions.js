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
// Popup placement and the listbox/aria-activedescendant wiring are shared with
// the :shortcode: popup in autocomplete.js.
import { webidColor, avatarInitial } from './util.js';
import { openListbox, closeListbox, setActiveOption, makeOption, keepInputFocus } from './autocomplete.js';

// Members whose display name starts with the query, then members with a later
// word starting with it ("Anna Bell" for "b"), each alphabetical. A letter in
// the middle of a name, or anywhere in a DID, is not a match. A member with no
// display name matches on the start of their key ("z6Mk..." after did:key:).
export function matchMembers(members, query, limit = 8) {
    const q = (query || '').toLowerCase();
    const prefix = [], word = [];
    for (const m of members || []) {
        const name = (m.display_name || '').toLowerCase();
        if (!name) {
            const key = (m.webid || '').toLowerCase().replace(/^did:key:/, '');
            if (key.startsWith(q)) word.push(m);
            continue;
        }
        if (name.startsWith(q)) prefix.push(m);
        else if (name.split(/[\s._-]+/).some(w => w.startsWith(q))) word.push(m);
    }
    const byName = (a, b) => (a.display_name || a.webid || '').localeCompare(b.display_name || b.webid || '');
    return prefix.sort(byName).concat(word.sort(byName)).slice(0, limit);
}

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
            const row = makeOption("mention", i);
            row.dataset.name = name;

            const avatar = document.createElement("span");
            avatar.className = "mo-avatar";
            avatar.setAttribute?.("aria-hidden", "true");
            avatar.style.background = webidColor(m.webid);
            avatar.textContent = avatarInitial(name);
            row.appendChild(avatar);

            const label = document.createElement("span");
            label.className = "ac-label";
            label.textContent = name;
            row.appendChild(label);

            if (m.status === "online") {
                const dot = document.createElement("span");
                dot.className = "mo-presence";
                dot.setAttribute?.("aria-hidden", "true");
                row.appendChild(dot);
            }
            dd.appendChild(row);
        });
        openListbox(inputEl, dd);
        setActiveOption(inputEl, dd.querySelectorAll(".mention-option"), 0);
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
        closeListbox(inputEl, document.getElementById("mention-dropdown"));
        state.mentionStart = -1;
    }

    function attach(el) {
        inputEl = el;
        keepInputFocus(document.getElementById("mention-dropdown"));

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
            const matches = matchMembers(members, val.slice(atPos + 1, caret));
            if (!matches.length) { closeMentionDropdown(); return; }
            state.mentionStart = atPos;
            _renderMentionDropdown(matches);
        });

        el.addEventListener("blur", () => closeMentionDropdown());

        el.addEventListener("keydown", e => {
            const dd = document.getElementById("mention-dropdown");
            if (!dd || dd.style.display === "none") return;
            const items = dd.querySelectorAll(".mention-option");
            if (!items.length) return;
            if (e.key === "ArrowDown") {
                e.preventDefault();
                state.mentionFocusIdx = Math.min(state.mentionFocusIdx + 1, items.length - 1);
                setActiveOption(inputEl, items, state.mentionFocusIdx);
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                state.mentionFocusIdx = Math.max(state.mentionFocusIdx - 1, 0);
                setActiveOption(inputEl, items, state.mentionFocusIdx);
            } else if (e.key === "Enter" || e.key === "Tab") {
                const name = items[state.mentionFocusIdx]?.dataset.name;
                if (name) { e.preventDefault(); e.stopImmediatePropagation?.(); _selectMention(name); }
            } else if (e.key === "Escape") {
                e.preventDefault(); e.stopPropagation?.(); closeMentionDropdown();
            }
        });
    }

    return { attach, closeMentionDropdown, _selectMention, _renderMentionDropdown, state };
}
