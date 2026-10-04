// Typing indicators — incoming "X is typing..." display + outgoing throttled
// "typing" command on keystroke. All state (who's typing, the outgoing
// throttle) is cluster-owned and lives in `state`.
//
// createTyping({ getSocket, getActiveView, resolveName }): getters return the
// reassignable host socket / activeView; resolveName(webid) returns a display
// name or a falsy value. Call attach(inputEl) once the message input exists;
// it wires the keystroke listener and starts the staleness sweep interval.

import { t } from './i18n.js';

// Short readable fallback for an unnamed sender. Every did:key starts with the
// same "did:key:z6Mk" prefix, so the tail is the only distinguishing part.
export function shortWebId(webid) {
    const id = String(webid || "");
    if (!id) return "?";
    if (/^https?:\/\//i.test(id)) {
        try { return new URL(id).hostname || id.slice(0, 24); } catch { return id.slice(0, 24); }
    }
    return id.length > 8 ? "…" + id.slice(-6) : id;
}

// "Alice is typing…" / "Alice and Bob are typing…" / "Several people are typing…"
export function typingText(webids, resolveName) {
    const names = webids.map((w) => (resolveName && resolveName(w)) || shortWebId(w));
    if (names.length === 0) return "";
    if (names.length === 1) return t("typing.one", { name: names[0] });
    if (names.length === 2) return t("typing.two", { a: names[0], b: names[1] });
    return t("typing.several");
}

export function createTyping({ getSocket, getActiveView, resolveName }) {
    const state = {
        typingUsers: {},      // webid -> timestamp of last "typing" event
        typingThrottled: false,
    };

    function handleTyping(event) {
        const id = event.room_id || event.cert_id;
        const activeView = getActiveView();
        if (!activeView || activeView.id !== id) return;
        state.typingUsers[event.from_webid] = Date.now();
        updateTypingDisplay();
    }

    function updateTypingDisplay() {
        const now = Date.now();
        const activeTyping = Object.keys(state.typingUsers).filter(
            (uid) => now - state.typingUsers[uid] < 4000
        );
        const el = document.getElementById("typing-indicator");
        if (!el) return;
        // Only touch the aria-live region when the text actually changes: the
        // 1s sweep would otherwise re-announce it every second.
        const text = typingText(activeTyping, resolveName);
        if (el.innerText !== text) el.innerText = text;
    }

    function attach(inputEl) {
        setInterval(updateTypingDisplay, 1000);
        if (!inputEl) return;
        inputEl.addEventListener("input", () => {
            const socket = getSocket();
            const activeView = getActiveView();
            if (!socket || !activeView || state.typingThrottled) return;
            const payload = { cmd: "typing" };
            if (activeView.type === "dm" || activeView.type === "local_dm") {
                payload.cert_id = activeView.id;
            } else {
                payload.room_id = activeView.id;
            }
            socket.send(JSON.stringify(payload));
            state.typingThrottled = true;
            setTimeout(() => { state.typingThrottled = false; }, 3000);
        });
    }

    return { handleTyping, updateTypingDisplay, attach, state };
}
