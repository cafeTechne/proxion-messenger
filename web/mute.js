// mute.js — per-thread mute toggle and its sidebar icon refresh.
//
// A factory. mutedThreads is a host-owned Set (read in several places across
// main.js — the renderer, dispatch, and sidebar context menu); it is injected
// by reference via getMutedThreads() and mutated in place, never reassigned.
// Returned functions are destructured into same-named bindings in main.js.
export function createMute({ getMutedThreads }) {

    function _saveMuted() {
        localStorage.setItem("proxion_muted_threads", JSON.stringify([...getMutedThreads()]));
    }

    function muteThread(id) {
        getMutedThreads().add(id);
        _saveMuted();
        _rerenderMuteIcon(id);
    }

    function unmuteThread(id) {
        getMutedThreads().delete(id);
        _saveMuted();
        _rerenderMuteIcon(id);
    }

    function _rerenderMuteIcon(id) {
        const muted = getMutedThreads();
        const el = document.getElementById(`nav-${id}`);
        if (!el) return;
        const badge = el.querySelector(".badge");
        if (badge) badge.style.display = muted.has(id) ? "none" : "";
        const icon = el.querySelector(".mute-icon");
        if (icon) icon.style.display = muted.has(id) ? "" : "none";
    }

    return { muteThread, unmuteThread, _saveMuted, _rerenderMuteIcon };
}
