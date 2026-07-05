// notifications.js — toasts, desktop/OS notifications, and the notification sound.
//
// A factory so the sound + OS-notification gate can read the host's live
// `soundEnabled` setting through getSoundEnabled() rather than capturing a stale
// snapshot. The returned object is destructured into same-named bindings in
// main.js, so existing call sites (showToast(...), playNotificationSound(), ...)
// keep working unchanged.
export function createNotifications({ getSoundEnabled, navigateToThread }) {

    // --------------- Toast ---------------
    function showToast(message, type) {
        const container = document.getElementById("toast-container");
        if (!container) return;
        const el = document.createElement("div");
        const bg = type === "error" ? "#dc2626" : type === "success" ? "#16a34a" : type === "warning" ? "#b45309" : "#1e293b";
        el.style.cssText = `background:${bg};color:#f8fafc;padding:10px 16px;border-radius:8px;` +
            `font-size:0.875rem;max-width:320px;box-shadow:0 4px 12px rgba(0,0,0,0.4);` +
            `pointer-events:auto;opacity:1;transition:opacity 0.3s`;
        el.textContent = message;
        container.appendChild(el);
        setTimeout(() => {
            el.style.opacity = "0";
            setTimeout(() => el.remove(), 300);
        }, 3500);
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
        if (window.__TAURI__?.invoke) {
            window.__TAURI__.invoke("show_notification", { title: safeTitle, body: safeBody }).catch(() => {});
            return;
        }
        if (!("Notification" in window)) return;
        if (Notification.permission !== "granted") return;
        if (document.hasFocus()) return;
        if (!getSoundEnabled()) return;
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
