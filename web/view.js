// View switching + sidebar list building — "open a thread / switch the active
// view". This is core slice 3: the functions that REASSIGN the central
// activeView/messageMap/allMessages/currentRoomMembers, so they take setters
// (setActiveView etc.) alongside getters. Host-owned maps that are mutated in
// place (unreadCounts, roomInviteUrls, _threadNames, roomCreatorOf, mutedThreads,
// peerDidToCertId) are injected by reference via getters. socket is resolved
// fresh inside each click handler because reconnect reassigns it.
//
// resetDateDivider() pokes rendering.state._lastRenderedDate = null (the render
// cursor lives in rendering.js); all other cross-module calls are injected.

import { escHtml } from './util.js';
import { podWriteReadState } from './pod.js';

import { t } from './i18n.js';

export function createView({
    getSocket,
    setActiveView, setMessageMap, setAllMessages, setCurrentRoomMembers, getAllMessages,
    getPeerDidToCertId, getThreadNames, getRoomInviteUrls, getRoomCreatorOf,
    getUnreadCounts, getMutedThreads,
    hideEmptyState, updateE2EStatus, updateIdentityFingerprint, closeMentionDropdown,
    updateSidebarBadge, sendUpdateLastRead, loadRoomHistory, toggleSidebar,
    updateDisappearBanner, requestRoomMembers, renderMembersPanel, updateVoiceChannels,
    openSidebarCtx, resetDateDivider,
}) {
    function renderContacts(contacts) {
        const peerDidToCertId = getPeerDidToCertId();
        const _threadNames = getThreadNames();
        const list = document.getElementById("contacts-list");
        const section = document.getElementById("contacts-section");
        if (!list || !section) return;
        list.innerHTML = "";
        if (!contacts || contacts.length === 0) { section.style.display = "none"; return; }
        section.style.display = "";
        hideEmptyState();
        for (const k in peerDidToCertId) delete peerDidToCertId[k]; // reset (mutate in place)
        contacts.forEach(c => {
            if (c.peer_did && c.certificate_id) peerDidToCertId[c.peer_did] = c.certificate_id;
            if (c.certificate_id) {
                _threadNames[c.certificate_id] = c.display_name || (c.peer_did || '').slice(8, 22) + '…';
            }
        });
        contacts.forEach(c => {
            const label = c.display_name || (c.peer_did || "").slice(8, 22) + "…";
            const li = document.createElement("li");
            li.className = "dm-item";
            li.title = c.peer_did || "";
            li.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25z"/></svg> ' + escHtml(label);
            li.onclick = () => openContactThread(c);
            list.appendChild(li);
        });
    }

    function openContactThread(contact) {
        const socket = getSocket();
        const unreadCounts = getUnreadCounts();
        hideEmptyState();
        const view = {
            type: "dm",
            id: contact.certificate_id,
            name: contact.display_name || (contact.peer_did || "").slice(8, 22) + "…",
            certId: contact.certificate_id,
            peerDid: contact.peer_did,
            peerWebid: contact.peer_did,
            local: false,
        };
        setActiveView(view);
        const header = document.getElementById("chat-header-name");
        if (header) header.textContent = view.name;
        updateE2EStatus(contact.peer_did);
        updateIdentityFingerprint(contact.peer_did);
        // Clear feed and reset message state
        const feed = document.getElementById("message-feed");
        if (feed) feed.innerHTML = "";
        resetDateDivider();
        setMessageMap({});
        setAllMessages([]);
        // Highlight sidebar item if present
        document.querySelectorAll("nav li").forEach(el => el.classList.remove("active"));
        const navEl = document.getElementById("nav-" + contact.certificate_id);
        if (navEl) navEl.classList.add("active");
        // Fetch history from gateway
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({cmd: "read_dm", cert_id: contact.certificate_id}));
            socket.send(JSON.stringify({cmd: "mark_read", thread_id: contact.certificate_id}));
            // Multi-device: learn the peer's per-device E2E keys so a DM can be
            // fanned out to each of their linked devices.
            if (contact.peer_did) {
                socket.send(JSON.stringify({cmd: "get_peer_device_keys", peer_webid: contact.peer_did}));
            }
        }
        // Clear unread badge
        unreadCounts[contact.certificate_id] = 0;
        updateSidebarBadge(contact.certificate_id);
    }

    function openLocalDmThread(id, name, peerWebid) {
        const socket = getSocket();
        const unreadCounts = getUnreadCounts();
        hideEmptyState();
        setActiveView({ type: "local_dm", id: id, name: name, local: true, peerWebid: peerWebid });
        document.getElementById("chat-header-name").innerText = "@ " + name;
        updateE2EStatus(peerWebid);
        updateIdentityFingerprint(peerWebid);
        document.getElementById("message-feed").innerHTML = "";
        resetDateDivider(); setMessageMap({}); setAllMessages([]);
        setCurrentRoomMembers([]);
        closeMentionDropdown();
        document.getElementById("members-toggle").style.display = "none";
        document.getElementById("leave-room-btn").style.display = "none";
        document.getElementById("delete-room-btn").style.display = "none";
        document.getElementById("members-panel").style.display = "none";
        document.getElementById("members-panel").classList.remove("mobile-open");
        document.getElementById("start-call-btn").style.display = "block";
        document.getElementById("invite-btn").style.display = "none";
        document.querySelectorAll("nav li").forEach(el => el.classList.remove("active"));
        const li = document.getElementById(`nav-${id}`);
        if (li) li.classList.add("active");
        unreadCounts[id] = 0;
        updateSidebarBadge(id);
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({cmd: "mark_read", thread_id: id}));
            sendUpdateLastRead(id);
            if (peerWebid) {
                socket.send(JSON.stringify({cmd: "get_peer_device_keys", peer_webid: peerWebid}));
            }
        }
        // Pod: persist read state
        const _lastMsgForRead = getAllMessages().filter(m => m.thread_id === id).at(-1);
        if (_lastMsgForRead) podWriteReadState(id, _lastMsgForRead.message_id).catch(() => {});
        loadRoomHistory(id);
        if (window.innerWidth <= 768) toggleSidebar();
    }

    function addRoomToSidebar(roomId, name, inviteUrl) {
        const roomInviteUrls = getRoomInviteUrls();
        const _threadNames = getThreadNames();
        if (inviteUrl) roomInviteUrls[roomId] = inviteUrl;
        if (name) _threadNames[roomId] = name;
        if (document.getElementById(`nav-${roomId}`)) return; // already added
        const hint = document.getElementById("room-list-empty-hint");
        if (hint) hint.remove();
        const list = document.getElementById("room-list");
        // The "No rooms yet / Create a room" CTA (class sidebar-empty, no id) is
        // added by the list rebuild; this incremental append path must clear it
        // too, or the CTA sits above the freshly created room forever.
        list.querySelectorAll(".sidebar-empty").forEach(el => el.remove());
        const li = document.createElement("li");
        li.id = `nav-${roomId}`;
        li.setAttribute("data-name", name);
        li.style.display = "flex";
        li.style.alignItems = "center";
        li.style.gap = "6px";
        li.innerHTML = `
            <div class="room-item-body">
                <div class="room-item-name">${escHtml(name)}</div>
                <div class="room-item-preview"></div>
            </div>
            <button data-sidebar-action="members" data-room-id="${escHtml(roomId)}" title="Members"
                    style="background:transparent;border:none;color:#8091a7;cursor:pointer;padding:2px 4px;font-size:0.85em;flex-shrink:0;"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z"/></svg></button>`;
        li.onclick = () => {
            const socket = getSocket();
            const unreadCounts = getUnreadCounts();
            const roomCreatorOf = getRoomCreatorOf();
            hideEmptyState();
            setActiveView({type: "local_room", id: roomId, name: name, local: true});
            document.getElementById("chat-header-name").innerText = "# " + name;
            updateIdentityFingerprint(null); // hide fingerprint bar in room views
            document.getElementById("message-feed").innerHTML = "";
            resetDateDivider();
            setMessageMap({});
            setAllMessages([]);
            setCurrentRoomMembers([]);
            closeMentionDropdown();
            document.getElementById("start-call-btn").style.display = "none";
            document.getElementById("invite-btn").style.display =
                roomInviteUrls[roomId] ? "inline-block" : "none";
            document.getElementById("members-toggle").style.display = "inline-block";
            document.getElementById("leave-room-btn").style.display = "inline-block";
            document.getElementById("delete-room-btn").style.display = roomCreatorOf.has(roomId) ? "inline-block" : "none";
            document.querySelectorAll("nav li").forEach(el => el.classList.remove("active"));
            li.classList.add("active");
            unreadCounts[roomId] = 0;
            updateSidebarBadge(roomId);
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({cmd: "mark_read", thread_id: roomId}));
                sendUpdateLastRead(roomId);
            }
            localStorage.setItem("proxion_seen_" + roomId, new Date().toISOString());
            if (window.innerWidth <= 768) toggleSidebar();
            // Load recent history from DB and fetch members
            loadRoomHistory(roomId, 100);
            requestRoomMembers(roomId);
            // Auto-show members panel if it was previously open
            if (document.getElementById("members-panel").style.display === "block") {
                renderMembersPanel([]);  // clear while loading
            }
        };
        list.appendChild(li);
    }

    function populateSidebar(listId, items, type) {
        const mutedThreads = getMutedThreads();
        const list = document.getElementById(listId);
        list.innerHTML = "";
        items.forEach(item => {
            const id = type === "dm" ? item.cert_id : item.id;
            const name = type === "dm" ? item.peer_webid.slice(0, 12) + "..." : item.name;

            const li = document.createElement("li");
            li.id = `nav-${id}`;
            li.setAttribute("data-name", name);
            li.style.display = "flex";
            li.style.alignItems = "center";
            li.style.justifyContent = "space-between";
            const nameSpan = document.createElement("span");
            nameSpan.className = "nav-label";
            nameSpan.style.flex = "1";
            nameSpan.textContent = name;
            li.appendChild(nameSpan);
            li.onclick = () => {
                const socket = getSocket();
                const roomInviteUrls = getRoomInviteUrls();
                const unreadCounts = getUnreadCounts();
                hideEmptyState();
                setActiveView({ type: type, id: id, name: name });
                document.getElementById("chat-header-name").innerText = (type === "room" ? "# " : "@ ") + name;
                document.getElementById("message-feed").innerHTML = "";
                resetDateDivider();
                setMessageMap({});
                setAllMessages([]);
                socket.send(JSON.stringify({
                    cmd: type === "dm" ? "read_dm" : "read_room",
                    [type === "dm" ? "cert_id" : "room_id"]: id
                }));
                if (window.innerWidth <= 768) toggleSidebar();

                document.getElementById("start-call-btn").style.display = type === "dm" ? "block" : "none";
                document.getElementById("invite-btn").style.display =
                    (type === "room" && roomInviteUrls[id]) ? "inline-block" : "none";
                const dtSel = document.getElementById("disappear-timer-select");
                const intBtn = document.getElementById("integrations-btn");
                if (dtSel) dtSel.style.display = type === "room" ? "inline-block" : "none";
                if (intBtn) intBtn.style.display = type === "room" ? "inline-block" : "none";
                if (type === "room" && socket) {
                    socket.send(JSON.stringify({cmd:"get_disappear_timer", room_id:id}));
                } else { updateDisappearBanner(0); }
                document.querySelectorAll("nav li").forEach(el => el.classList.remove("active"));
                li.classList.add("active");

                // Clear unread
                unreadCounts[id] = 0;
                updateSidebarBadge(id);
                if (socket && socket.readyState === WebSocket.OPEN)
                    socket.send(JSON.stringify({cmd: "mark_read", thread_id: id}));

                if (type === "room") {
                    updateVoiceChannels(id);
                }
            };
            li.addEventListener("contextmenu", e => openSidebarCtx(e, id));
            // Mute icon
            const muteIcon = document.createElement("span");
            muteIcon.className = "mute-icon";
            muteIcon.title = "Muted";
            muteIcon.style.cssText = `display:${mutedThreads.has(id) ? "" : "none"};font-size:0.75em;color:#8091a7;margin-left:4px;flex-shrink:0;`;
            muteIcon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9.143 17.082a24.248 24.248 0 0 0 3.844.148m-3.844-.148a23.856 23.856 0 0 1-5.455-1.31 8.964 8.964 0 0 0 2.3-5.542m3.155 6.852a3 3 0 0 0 5.667 1.97m1.965-2.277L21 21m-4.225-4.225a23.81 23.81 0 0 0 3.536-1.003 8.967 8.967 0 0 1-2.312-6.022V9A6 6 0 0 0 9.239 3.477L3 3m6.239.477A5.965 5.965 0 0 0 6 9v.75a8.966 8.966 0 0 1-2.312 6.022"/></svg>';
            li.appendChild(muteIcon);
            list.appendChild(li);
            updateSidebarBadge(id); // apply existing unreads
        });
        // G3/G4: an empty list is a dead end — show an actionable CTA that fires
        // the section's existing header button (create-room / add-peer).
        if (items.length === 0) {
            const cta = _SIDEBAR_EMPTY[listId];
            if (cta) {
                const li = document.createElement("li");
                li.className = "sidebar-empty";
                li.innerHTML = `<p class="state-msg state-empty">${t(cta.msg)}</p>` +
                    `<button type="button" class="state-cta">${t(cta.label)}</button>`;
                li.querySelector("button").onclick = () => document.getElementById(cta.btn)?.click();
                list.appendChild(li);
            }
        }
    }

    // Sidebar empty-state CTAs, keyed by list id (G3/G4).
    const _SIDEBAR_EMPTY = {
        "room-list": { msg: "sidebar.empty.rooms", label: "sidebar.empty.createRoom", btn: "create-room-btn" },
        "dm-list":   { msg: "sidebar.empty.dms", label: "sidebar.empty.addSomeone", btn: "add-peer-btn" },
    };

    // R18.2.2: navigate to a thread from tray unread click (clicks the nav item,
    // whose onclick is the view-switcher above).
    function _navigateToThread(threadId) {
        if (!threadId) return;
        const li = document.getElementById(`nav-${CSS.escape(threadId)}`);
        if (li) li.click();
    }

    return {
        renderContacts, openContactThread, openLocalDmThread,
        addRoomToSidebar, populateSidebar, _navigateToThread,
    };
}
