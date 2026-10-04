// notifications.js — toasts, desktop/OS notifications, and the notification sound.
//
// A factory so the sound + OS-notification gate can read the host's live
// `soundEnabled` setting through getSoundEnabled() rather than capturing a stale
// snapshot. The returned object is destructured into same-named bindings in
// main.js, so existing call sites (showToast(...), playNotificationSound(), ...)
// keep working unchanged.
import { announce } from './a11y.js';
import { t } from './i18n.js';
import { icon } from './icons.js';

export function createNotifications({ getSoundEnabled, getDesktopNotifEnabled, navigateToThread }) {
    // Desktop notifications default to enabled if the host doesn't inject a
    // getter (keeps older call sites / tests working). They are INDEPENDENT of
    // the sound chime — muting sound must not silence visible notifications.
    const desktopOn = () => (typeof getDesktopNotifEnabled === "function" ? getDesktopNotifEnabled() : true);

    // --------------- Toast ---------------
    // At most MAX_TOASTS are visible (the oldest drops first) so a burst never
    // walls off the composer or a dialog. A repeat of a toast that is still on
    // screen bumps a "x2" counter on it instead of stacking a copy.
    const MAX_TOASTS = 3;
    const _toasts = [];   // live entries, oldest first

    function _kindOf(type) {
        return type === "error" || type === "success" || type === "warning" ? type : "info";
    }

    // showToast(message, type?, { action?: { label, onClick } })
    function showToast(message, type, opts = {}) {
        const container = document.getElementById("toast-container");
        if (!container) return;
        const text = String(message ?? "");
        const kind = _kindOf(type);
        const action = opts && opts.action && opts.action.label ? opts.action : null;
        // Screen readers: the toast is a visual popup, so mirror it to a live
        // region (assertive for errors so they interrupt, polite otherwise).
        announce(text, kind === "error");

        const dup = _toasts.find(e => e.text === text && e.kind === kind);
        if (dup) {
            dup.count++;
            dup.countEl.textContent = t('toast.count', { count: dup.count });
            dup.countEl.hidden = false;
            dup.rearm();
            return;
        }
        while (_toasts.length >= MAX_TOASTS) _toasts[0].remove(true);
        // Sit just below the chat header (its actions stay clickable). A page
        // banner above the app pushes the header down, so measure it rather
        // than trusting the CSS fallback offset.
        const hdrBottom = document.getElementById("chat-header")?.getBoundingClientRect?.().bottom;
        if (hdrBottom > 0) container.style.top = `${Math.round(hdrBottom + 8)}px`;

        const el = document.createElement("div");
        el.className = `toast toast--${kind}`;
        const msgEl = document.createElement("span");
        msgEl.className = "toast__msg";
        msgEl.textContent = text;
        el.appendChild(msgEl);
        const countEl = document.createElement("span");
        countEl.className = "toast__count";
        countEl.hidden = true;
        el.appendChild(countEl);
        if (action) {
            const actBtn = document.createElement("button");
            actBtn.type = "button";
            actBtn.className = "toast__action";
            actBtn.textContent = action.label;
            actBtn.addEventListener("click", (e) => {
                e.stopPropagation?.();
                try { action.onClick?.(); } finally { entry.remove(); }
            });
            el.appendChild(actBtn);
        }
        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "toast__close";
        closeBtn.setAttribute("aria-label", t('toast.dismiss'));
        closeBtn.title = t('toast.dismiss');
        closeBtn.innerHTML = icon('x-mark', { size: 14 });
        closeBtn.addEventListener("click", (e) => { e.stopPropagation?.(); entry.remove(); });
        el.appendChild(closeBtn);
        container.appendChild(el);

        let hideTimer = null;
        // Errors linger longer (they may need reading or acting on), and an
        // error that offers an action stays until the user deals with it.
        // Hovering or focusing pauses the countdown so a toast never vanishes
        // mid-read, and clicking the body still dismisses it.
        const sticky = kind === "error" && !!action;
        const dwell = kind === "error" ? 8000 : 3500;
        const disarm = () => { if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; } };
        const arm = () => { disarm(); if (!sticky) hideTimer = setTimeout(() => entry.remove(), dwell); };
        const entry = {
            el, text, kind, count: 1, countEl, removed: false,
            rearm: arm,
            remove(immediate = false) {
                if (entry.removed) return;
                entry.removed = true;
                disarm();
                const i = _toasts.indexOf(entry);
                if (i >= 0) _toasts.splice(i, 1);
                if (immediate) { el.remove(); return; }
                el.classList.add("toast--leaving");
                setTimeout(() => el.remove(), 300);
            },
        };
        _toasts.push(entry);
        el.addEventListener("mouseenter", disarm);
        el.addEventListener("mouseleave", arm);
        el.addEventListener("focusin", disarm);
        el.addEventListener("focusout", arm);
        el.addEventListener("click", () => entry.remove());
        arm();
    }

    function playNotificationSound() {
        if (!getSoundEnabled()) return;
        try {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            if (audioCtx.state === 'suspended') audioCtx.resume();
            const oscillator = audioCtx.createOscillator();
            const gainNode = audioCtx.createGain();

            oscillator.type = 'sine';
            oscillator.frequency.setValueAtTime(880, audioCtx.currentTime);
            oscillator.frequency.exponentialRampToValueAtTime(440, audioCtx.currentTime + 0.15);

            gainNode.gain.setValueAtTime(0.05, audioCtx.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.15);

            oscillator.connect(gainNode);
            gainNode.connect(audioCtx.destination);

            oscillator.start();
            oscillator.stop(audioCtx.currentTime + 0.2);
        } catch (e) {
            console.warn("Audio Context failed", e);
        }
    }

    // ── Push notifications ──
    function requestNotifPermission() {
        if ("Notification" in window && Notification.permission === "default") {
            Notification.requestPermission();
        }
    }

    function showOsNotification(title, body, threadId) {
        const safeTitle = String(title || "").slice(0, 80);
        const safeBody = String(body || "").slice(0, 80);
        if (!desktopOn()) return;
        if (window.__TAURI__?.invoke) {
            window.__TAURI__.invoke("show_notification", { title: safeTitle, body: safeBody }).catch(() => {});
            return;
        }
        if (!("Notification" in window)) return;
        if (Notification.permission !== "granted") return;
        if (document.hasFocus()) return;
        const n = new Notification(safeTitle, {
            body: safeBody,
            tag: threadId,
        });
        n.onclick = () => {
            window.focus();
            // Open the conversation the notification was about (was: focus only).
            if (threadId && typeof navigateToThread === "function") navigateToThread(threadId);
            n.close();
        };
        setTimeout(() => n.close(), 6000);
    }

    return { showToast, playNotificationSound, requestNotifPermission, showOsNotification };
}
