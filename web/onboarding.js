// onboarding.js — the first-run setup wizard (the #onboarding-modal step flow)
// and the pod-credential form it hosts.
//
// A factory: reassignable host state (the WebSocket) is read live via
// getSocket(); the few host helpers it needs (setPodBanner, showToast,
// showCopyModal) are injected. podWriteProfile is imported directly. The
// returned functions are destructured into same-named bindings in main.js, so
// the existing setupEventListeners wiring keeps working unchanged.
import { podWriteProfile } from './pod.js';

export function createOnboarding({ getSocket, setPodBanner, showToast, showCopyModal, showConfirm }) {

    function openSettingsToPod() {
        document.getElementById("settings-btn").click();
        // Pod controls live in the Advanced section (G1) — expand it and jump there.
        const adv = document.getElementById("settings-advanced");
        if (adv) {
            adv.style.display = "";
            document.getElementById("settings-advanced-toggle")?.setAttribute("aria-expanded", "true");
            const caret = document.getElementById("settings-advanced-caret");
            if (caret) caret.textContent = "▴";
            (document.getElementById("settings-pod-disconnected") ||
             document.getElementById("settings-pod-connected"))?.scrollIntoView({ block: "nearest" });
        }
    }

    function obPodMode(mode) {
        const customInput = document.getElementById("ob-pod-css-url");
        if (mode === "docker") {
            // Pre-fill with the injected CSS URL if available, else local default
            const defaultCss = document.querySelector('meta[name="x-css-default-url"]')?.content || localStorage.getItem("proxion_css_default_url") || "";
            const url = defaultCss && !defaultCss.includes("localhost") && !defaultCss.includes("127.0.0.1")
                ? defaultCss
                : "http://localhost:3000";
            if (customInput && !customInput.value) customInput.value = url;
        }
    }

    function showOnboarding() {
        document.getElementById("onboarding-modal").style.display = "flex";
        const defaultCss = document.querySelector('meta[name="x-css-default-url"]')?.content || localStorage.getItem("proxion_css_default_url") || "";
        if (defaultCss && !defaultCss.includes("localhost") && !defaultCss.includes("127.0.0.1")) {
            // External CSS: pre-fill the custom URL input for easy sign-in
            const customInput = document.getElementById("ob-pod-css-url");
            if (customInput && !customInput.value) customInput.value = defaultCss;
        } else if (defaultCss) {
            // Localhost CSS (Docker self-hosted)
            obPodMode("docker");
        }
    }

    function obGoto(step, opts = {}) {
        for (let i = 1; i <= 6; i++) {
            const el = document.getElementById(`ob-step-${i}`);
            if (el) el.style.display = "none";
        }
        const target = document.getElementById(`ob-step-${step}`);
        if (target) target.style.display = "block";
        if (step === 6) {
            const obAddr = document.getElementById("ob-my-addr");
            if (obAddr) {
                const addr = window.proxionAddress || localStorage.getItem("proxion_my_address") || "";
                obAddr.textContent = addr || "(connecting…)";
            }
        }
    }

    function obStep3() {
        const selected = document.querySelector('input[name="ob-status"]:checked');
        const status = selected ? selected.value : 'online';
        localStorage.setItem('proxion_status', status);
        const socket = getSocket();
        if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ cmd: 'set_presence', status }));
        }
        obGoto(4);
    }

    function obStep2() {
        const name = document.getElementById("ob-name").value.trim();
        if (!name) { document.getElementById("ob-name").focus(); return; }
        localStorage.setItem("proxion_display_name", name);
        document.getElementById("username").innerText = name;
        const socket = getSocket();
        if (socket) socket.send(JSON.stringify({ cmd: "set_identity", display_name: name }));
        podWriteProfile({ displayName: name }).catch(() => {});
        obGoto(3);
    }

    function finishOnboarding() {
        document.getElementById("onboarding-modal").style.display = "none";
        const socket = getSocket();
        if (socket && socket.readyState === WebSocket.OPEN)
            socket.send(JSON.stringify({ cmd: "set_presence", status: "online" }));
        // R18.1.2: enable autostart the first time setup completes
        if (window.__TAURI__?.invoke && !localStorage.getItem('proxion_wizard_done')) {
            window.__TAURI__.invoke('plugin:autostart|enable').catch(() => {});
        }
        localStorage.setItem('proxion_wizard_done', '1');
    }

    function obSkipPod() {
        const msg = "Without a pod, your messages won't sync across devices and won't be backed up. Continue without a pod?";
        const proceed = () => {
            localStorage.setItem("proxion_pod_setup_skipped", "1");
            localStorage.removeItem("proxion_pod_banner_dismissed");
            setPodBanner(true);
            obGoto(5);
        };
        // Styled in-app confirm (native window.confirm looked out of place at the
        // wizard's key decision point); fall back if the host didn't inject it.
        if (showConfirm) showConfirm(msg, proceed);
        else if (window.confirm(msg)) proceed();
    }

    // R16: pod credential form logic
    let _obSelectedCssUrl = "";
    function obSelectProvider(cssUrl) {
        _obSelectedCssUrl = cssUrl;
        document.getElementById("ob-pod-providers").style.display = "none";
        const form = document.getElementById("ob-pod-cred-form");
        form.style.display = "block";
        const label = document.getElementById("ob-pod-provider-label");
        const urlInput = document.getElementById("ob-pod-css-url");
        if (cssUrl === "custom") {
            label.textContent = "Enter your Community Solid Server URL, email, and password.";
            urlInput.style.display = "block";
            urlInput.value = "";
        } else {
            label.textContent = `Enter your ${cssUrl} account email and password.`;
            urlInput.style.display = "none";
            urlInput.value = cssUrl;
        }
        document.getElementById("ob-pod-email").value = "";
        document.getElementById("ob-pod-password").value = "";
        document.getElementById("ob-pod-status").textContent = "";
        const cont = document.getElementById("ob-pod-continue-btn");
        cont.disabled = true;
        cont.style.opacity = "0.45";
        cont.style.cursor = "not-allowed";
    }

    async function obPodTestConnection() {
        const cssUrl = (_obSelectedCssUrl === "custom"
            ? (document.getElementById("ob-pod-css-url")?.value || "").trim().rstrip?.("/") || (document.getElementById("ob-pod-css-url")?.value || "").trim().replace(/\/$/, "")
            : _obSelectedCssUrl);
        const email = (document.getElementById("ob-pod-email")?.value || "").trim();
        const password = (document.getElementById("ob-pod-password")?.value || "");
        const statusEl = document.getElementById("ob-pod-status");
        const testBtn = document.getElementById("ob-pod-test-btn");
        const contBtn = document.getElementById("ob-pod-continue-btn");
        if (!cssUrl || !email || !password) {
            statusEl.textContent = "Please fill in all fields.";
            statusEl.style.color = "#f87171";
            return;
        }
        testBtn.textContent = "Testing…";
        testBtn.disabled = true;
        statusEl.textContent = "";
        try {
            const gwBase = (localStorage.getItem("proxion_gateway_http_url") || "http://127.0.0.1:8080").replace(/\/$/, "");
            const resp = await fetch(`${gwBase}/setup/pod`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ css_url: cssUrl, email, password }),
            });
            const data = await resp.json();
            if (data.status === "ok") {
                statusEl.textContent = "✓ Connected!";
                statusEl.style.color = "#4ade80";
                contBtn.disabled = false;
                contBtn.style.opacity = "1";
                contBtn.style.cursor = "pointer";
                // R16.3.2: persist to OS keychain if running in Tauri
                if (window.__TAURI__?.invoke) {
                    window.__TAURI__.invoke('store_pod_credentials', { cssUrl, email, password }).catch(() => {});
                }
            } else {
                statusEl.textContent = data.message || "Connection failed.";
                statusEl.style.color = "#f87171";
            }
        } catch (e) {
            statusEl.textContent = "Couldn't connect. Is Proxion running?";
            statusEl.style.color = "#f87171";
        }
        testBtn.textContent = "Test connection";
        testBtn.disabled = false;
    }

    function copyObInviteUrl() {
        const el = document.getElementById("ob-room-invite-url");
        const url = el?.textContent || "";
        if (!url) return;
        navigator.clipboard.writeText(url).then(() => showToast("Invite link copied!")).catch(() => {
            showCopyModal(url);
        });
    }

    function obStep4Create() {
        window._obFromOnboarding = true;
        document.getElementById("room-create-form").style.display = "";
        document.getElementById("room-invite-result").style.display = "none";
        document.getElementById("room-name-input").value = "";
        document.getElementById("room-history-toggle").checked = false;
        document.getElementById("room-create-modal").style.display = "flex";
        setTimeout(() => document.getElementById("room-name-input").focus(), 50);
    }

    function obStep4Join() {
        let val = document.getElementById("ob-invite-code").value.trim();
        const socket = getSocket();
        if (!val || !socket) return;
        let code = val;
        try {
            const u = new URL(val);
            const p = u.searchParams.get("join");
            if (p) code = p;
        } catch (_) {}
        socket.send(JSON.stringify({ cmd: "join_room", code: code }));
        finishOnboarding();
    }

    return {
        openSettingsToPod, obPodMode, showOnboarding, obGoto, obStep3, obStep2,
        finishOnboarding, obSkipPod, obSelectProvider, obPodTestConnection,
        copyObInviteUrl, obStep4Create, obStep4Join,
    };
}
