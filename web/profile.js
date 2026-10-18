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
import { webidColor, timeAgo, escHtml, avatarInitial, dmFallbackName, gatewayUrl } from './util.js';

export function createProfile({ getSocket, showToast, getUserPresence, getMessageMap, isBlocked, isSelf, isContact, openContactByDid }) {
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
        const shortName = avatarInitial(displayName || webid.slice(0, 12));

        // Update card contents
        document.getElementById("profile-name").textContent = displayName || webid.slice(0, 12);
        document.getElementById("profile-webid").textContent = webid;
        // Heartbeat presence (R104) is coarse: for a contact who is not currently
        // online, show "last seen <time>" from their last heartbeat rather than a
        // bare "offline", so the staleness is honest.
        {
            const _st = presenceData.status || "offline";
            const _statusEl = document.getElementById("profile-status-text");
            if ((_st === "offline" || _st === "away") && presenceData.updated_at) {
                _statusEl.textContent = t('presence.lastSeen', { time: timeAgo(presenceData.updated_at) });
            } else {
                _statusEl.textContent = _st;
            }
        }

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
        avatarEl.style.color = "var(--avatar-initial)";
        avatarEl.textContent = shortName;

        // R65: reflect block state on the block/unblock button
        const blockBtn = document.getElementById("profile-block-btn");
        if (blockBtn) {
            const bl = isBlocked?.(webid);
            blockBtn.textContent = bl ? t("btn.unblock") : t("btn.block");
        }

        // Position popover near click point
        const card = document.getElementById("profile-card");
        // Your own card: nothing to DM or block.
        const self = !!isSelf?.(webid);
        for (const sel of [".profile-actions", ".profile-divider"]) {
            const el = card.querySelector?.(sel);
            if (el) el.hidden = self;
        }
        card.classList.add("show");
        card.style.left = Math.min(x, window.innerWidth - 310) + "px";
        card.style.top = Math.min(y, window.innerHeight - 250) + "px";
    }

    function profileCardOpenDM() {
        const webid = state.profileCardActive;
        if (!webid) return;
        hideProfileCard();

        // A friend's chat is their contact thread; a second thread keyed by their
        // id would split the conversation.
        if (openContactByDid && openContactByDid(webid)) return;

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

    // displayName is what the caller already shows for this person (the
    // message author, the member row); the gateway profile, when it has one,
    // fills in the rest. Live presence wins over the profile's stored status.
    function showContactProfile(webid, displayName = '') {
        if (!webid) return;
        const panel = document.getElementById("contact-profile-panel");
        if (!panel) return;
        const live = getUserPresence?.()[webid] || {};
        const cached = {
            did: webid,
            display_name: displayName,
            status: live.status || 'offline',
            status_message: live.status_message || '',
            gateway_url: '',
            fingerprint: '',
        };
        _renderContactProfile(cached);
        panel.style.display = '';
        fetch(gatewayUrl(`/profile/${encodeURIComponent(webid)}`))
            .then(r => r.ok ? r.json() : null)
            .then(d => {
                if (!d || state._contactProfileDid !== webid) return;
                _renderContactProfile({ ...cached, ...d,
                    display_name: d.display_name || displayName,
                    status: live.status || d.status || 'offline' });
            })
            .catch(() => {});
    }

    function _renderContactProfile(d) {
        state._contactProfileDid = d.did || null;
        const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val || ''; };
        // A row with nothing to say (no known gateway, no fingerprint) is hidden
        // with its label rather than shown empty or as a placeholder.
        const setRow = (id, val) => {
            setText(id, val);
            for (const el of [document.getElementById(id), document.getElementById(id + '-label')]) {
                if (el) el.hidden = !val;
            }
        };
        const name = d.display_name || dmFallbackName(d.did || '');
        setText('contact-profile-name', name);
        setText('contact-profile-did', d.did || '');
        setRow('contact-profile-gateway', d.gateway_url || '');
        setRow('contact-profile-fingerprint', d.fingerprint || '');
        setRow('contact-profile-status-msg', d.status_message || '');
        const statusEl = document.getElementById('contact-profile-status');
        if (statusEl) {
            const colors = { online: 'var(--color-success-soft)', away: 'var(--color-warning-soft)', busy: 'var(--color-danger-soft)', offline: 'var(--slate-600)' };
            const st = d.status || 'offline';
            statusEl.innerHTML = `<span style="color:${colors[st] || 'var(--slate-600)'}">&#x25cf;</span> ${escHtml(st)}`;
        }
        const avatarEl = document.getElementById('contact-profile-avatar');
        if (avatarEl) {
            // Same initial and colour as this person's message avatar.
            avatarEl.textContent = avatarInitial(name || '?');
            if (d.did) avatarEl.style.background = webidColor(d.did);
            avatarEl.style.color = 'var(--avatar-initial)';
        }
        // No "Send DM" to yourself, and "Remove contact" only for a contact.
        const self = !!isSelf?.(d.did);
        const dmBtn = document.getElementById('contact-profile-dm-btn');
        if (dmBtn) { dmBtn.dataset.webid = d.did || ''; dmBtn.hidden = self; }
        const rmBtn = document.getElementById('contact-profile-remove-btn');
        if (rmBtn) rmBtn.hidden = self || !(isContact?.(d.did) ?? true);
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
