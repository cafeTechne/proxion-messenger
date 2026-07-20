// profile.js — user status display: presence updates, the hover profile-card
// popover, and the contact-profile side panel.
//
// A factory. userPresence and messageMap are host-owned shared objects (the
// dispatch and message renderer also read/write them), injected by reference
// via getters and mutated in place. _profileCardActive is cluster-owned and
// lives in `state`. socket is read live via a getter; showToast is injected;
// webidColor is imported. Returned functions are destructured into same-named
// bindings in main.js so the dispatch and listener wiring keep working.
import { t } from './i18n.js';
import { webidColor } from './util.js';

export function createProfile({ getSocket, showToast, getUserPresence, getMessageMap, isBlocked }) {
    const state = { profileCardActive: null };

    function handlePresenceUpdate(event) {
        const { webid, status, updated_at } = event;
        if (!webid) return;

        // Store presence data
        getUserPresence()[webid] = { status, updated_at };

        // Re-render all messages from this user to update presence indicators
        const messageMap = getMessageMap();
        const feed = document.getElementById("message-feed");
        const messages = feed.querySelectorAll(".message");
        messages.forEach(msgEl => {
            const msgId = msgEl.getAttribute("data-message-id");
            const msg = messageMap[msgId];
            if (msg && msg.from_webid === webid) {
                // Find the avatar-presence dot and update it
                const presenceDot = msgEl.querySelector(".avatar-presence");
                if (presenceDot) {
                    presenceDot.className = "avatar-presence " + (status === "online" ? "online" : status === "away" ? "away" : status === "busy" ? "busy" : "");
                    presenceDot.title = status;
                }
            }
        });
    }

    function updatePresence(event) { handlePresenceUpdate(event); }

    function showProfileCard(webid, displayName, x, y) {
        if (!webid) return;
        state.profileCardActive = webid;

        // Get presence status for this user
        const presenceData = getUserPresence()[webid] || { status: "offline" };
        const avatarColor = webidColor(webid);
        const shortName = (displayName || webid.slice(0, 12))[0].toUpperCase();

        // Update card contents
        document.getElementById("profile-name").textContent = displayName || webid.slice(0, 12);
        document.getElementById("profile-webid").textContent = webid;
        document.getElementById("profile-status-text").textContent = presenceData.status || "offline";

        // Display custom status message if available
        const customStatusEl = document.getElementById("profile-custom-status");
        if (presenceData.status_message && presenceData.status_message.trim()) {
            customStatusEl.textContent = presenceData.status_message;
            customStatusEl.style.display = "block";
        } else {
            customStatusEl.textContent = "";
            customStatusEl.style.display = "none";
        }

        const statusDot = document.getElementById("profile-status-dot");
        statusDot.className = "profile-status-dot " + (presenceData.status === "online" ? "online" : presenceData.status === "away" ? "away" : presenceData.status === "busy" ? "busy" : "");

        const avatarEl = document.getElementById("profile-avatar-el");
        avatarEl.style.background = avatarColor;
        avatarEl.textContent = shortName;

        // R65: reflect block state on the block/unblock button
        const blockBtn = document.getElementById("profile-block-btn");
        if (blockBtn) {
            const bl = isBlocked?.(webid);
            blockBtn.textContent = bl ? t("btn.unblock") : t("btn.block");
        }

        // Position popover near click point
        const card = document.getElementById("profile-card");
        card.classList.add("show");
        card.style.left = Math.min(x, window.innerWidth - 310) + "px";
        card.style.top = Math.min(y, window.innerHeight - 250) + "px";
    }

    function profileCardOpenDM() {
        const webid = state.profileCardActive;
        if (!webid) return;
        hideProfileCard();

        // Check if a DM thread already exists with this webid
        const navId = "local-" + webid.replace(/[^a-zA-Z0-9]/g, "-");
        const existingLi = document.getElementById(`nav-${navId}`);

        if (existingLi) {
            // Click existing DM
            existingLi.click();
        } else {
            // Trigger resolve_did which will create a DM sidebar entry
            const socket = getSocket();
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ cmd: "resolve_did", did: webid }));
                // The resolve_did handler will add it to sidebar and we can click it
                setTimeout(() => {
                    const newLi = document.getElementById(`nav-${navId}`);
                    if (newLi) newLi.click();
                }, 100);
            }
        }
    }

    function hideProfileCard() {
        const card = document.getElementById("profile-card");
        card.classList.remove("show");
        state.profileCardActive = null;
    }

    function showContactProfile(webid) {
        if (!webid) return;
        const panel = document.getElementById("contact-profile-panel");
        if (!panel) return;
        const cached = {
            did: webid,
            display_name: '',
            status: 'offline',
            status_message: '',
            gateway_url: '',
            fingerprint: '',
        };
        _renderContactProfile(cached);
        panel.style.display = '';
        fetch(`/profile/${encodeURIComponent(webid)}`)
            .then(r => r.ok ? r.json() : null)
            .then(d => { if (d) _renderContactProfile(d); })
            .catch(() => {});
    }

    function _renderContactProfile(d) {
        const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val || ''; };
        setText('contact-profile-name', d.display_name || d.did?.slice(-12) || '');
        setText('contact-profile-did', d.did || '');
        setText('contact-profile-gateway', d.gateway_url || '(unknown gateway)');
        setText('contact-profile-fingerprint', d.fingerprint || '');
        setText('contact-profile-status-msg', d.status_message || '');
        const statusEl = document.getElementById('contact-profile-status');
        if (statusEl) {
            const colors = { online: '#4ade80', away: '#fbbf24', busy: '#f87171', offline: '#475569' };
            const st = d.status || 'offline';
            statusEl.innerHTML = `<span style="color:${colors[st] || '#475569'}">&#x25cf;</span> ${st}`;
        }
        const avatarEl = document.getElementById('contact-profile-avatar');
        if (avatarEl) {
            const initials = (d.display_name || d.did || '?').slice(0, 2).toUpperCase();
            avatarEl.textContent = initials;
        }
        const dmBtn = document.getElementById('contact-profile-dm-btn');
        if (dmBtn) dmBtn.dataset.webid = d.did || '';
        const didEl = document.getElementById('contact-profile-did');
        if (didEl) {
            didEl.onclick = () => navigator.clipboard.writeText(d.did || '').then(() => showToast(t('profile.didCopied')));
        }
    }

    return {
        handlePresenceUpdate, updatePresence, showProfileCard, profileCardOpenDM,
        hideProfileCard, showContactProfile, _renderContactProfile, state,
    };
}
