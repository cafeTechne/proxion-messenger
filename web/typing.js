// Typing indicators — incoming "X is typing..." display + outgoing throttled
// "typing" command on keystroke. All state (who's typing, the outgoing
// throttle) is cluster-owned and lives in `state`.
//
// createTyping({ getSocket, getActiveView }) — getters return the reassignable
// host socket / activeView. Call attach(inputEl) once the message input exists;
// it wires the keystroke listener and starts the staleness sweep interval.

export function createTyping({ getSocket, getActiveView }) {
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
        if (activeTyping.length > 0) {
            el.innerText = `${activeTyping[0].slice(0, 8)}... is typing...`;
        } else {
            el.innerText = "";
        }
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
