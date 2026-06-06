import { solidSession, initSolidAuth, solidLogin, solidLogout, podStorageRoot, discoverStorageRoot } from './auth.js';
import {
    initE2E, e2eSupported, isE2EEnabled, myX25519PubB64u, cachePeerPub,
    ratchetEncrypt, ratchetDecrypt, fetchAndCachePeerPub, E2EDecryptError, safetyNumber,
} from './e2e.js';
import { podWriteMessageWithIndex, podWriteRoomMeta, podReadMessages, podSetContainerAcl,
         ensureProxionContainer, podWriteProfile, podReadProfile,
         podWriteMessageJsonLd, podDeleteMessage, podWriteRoomMembers,
         podWriteReactions, podWriteReadState,
         podUploadFile, podUploadVoiceAudio,
         podWriteScheduled, podDeleteScheduled,
         podWriteWebhook, podDeleteWebhook,
         podWriteContact, podReadContacts, podDeleteContact,
         podWriteInvite, podReadInvites, podDeleteInvite,
         podReadRoomIndex, _podUpdateRoomIndex,
         podReadDmIndex, _podUpdateDmIndex } from './pod.js';

        const WS_URL = (() => {
            const metaUrl = document.querySelector('meta[name="x-gateway-url"]')?.content;
            const stored = metaUrl || localStorage.getItem("proxion_gateway_url") || "ws://127.0.0.1:7474";
            if (metaUrl) localStorage.setItem("proxion_gateway_url", metaUrl);
            // Normalize "localhost" â†’ "127.0.0.1": on Windows, localhost can resolve to ::1
            // (IPv6) first but the gateway only binds IPv4 (0.0.0.0), causing silent failures.
            return stored.replace(/^(wss?):\/\/localhost([:\/])/i, "$1://127.0.0.1$2")
                         .replace(/^(wss?):\/\/localhost$/i,      "$1://127.0.0.1");
        })();
        // Allowed top-level keys per command — strips unrecognized keys before sending
        const CMD_ALLOWED_KEYS = {
            connect_css:      new Set(["cmd","css_url","email","password"]),
            create_webhook:   new Set(["cmd","thread_id","direction","url","bot_name"]),
            send_file:        new Set(["cmd","cert_id","room_id","filename","mime_type","data_b64"]),
            schedule_message: new Set(["cmd","thread_id","content","send_at"]),
        };

        function sendCmd(cmd, payload) {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;
            const allowed = CMD_ALLOWED_KEYS[cmd];
            const out = { cmd, ...payload };
            if (allowed) {
                for (const key of Object.keys(out)) {
                    if (!allowed.has(key)) delete out[key];
                }
            }
            socket.send(JSON.stringify(out));
        }

        // Pre-fill name from previous session
        (function() {
            const n = localStorage.getItem("proxion_display_name");
            if (n) { const el = document.getElementById("ob-name"); if (el) el.value = n; }
            const addr = localStorage.getItem("proxion_my_address");
            if (addr) {
                // updateMyAddressBar is defined later; defer until functions are loaded
                setTimeout(() => { if (typeof updateMyAddressBar === "function") updateMyAddressBar(addr); }, 0);
            }
        })();

        // R8.3.1: pre-fill add-contact modal from ?from= URL param
        (function() {
            const params = new URLSearchParams(window.location.search);
            const fromAddr = params.get('from');
            if (fromAddr) {
                const inp = document.getElementById('add-peer-input');
                if (inp) inp.value = fromAddr;
                // Trigger the modal open after a short delay (wait for connection)
                setTimeout(() => {
                    const btn = document.getElementById('add-peer-btn');
                    if (btn) btn.click();
                }, 1500);
                // Clean URL
                history.replaceState({}, '', window.location.pathname);
            }
        })();

        // wizard-overlay removed: onboarding-modal is the single first-run wizard
        const RENDER_WINDOW = 100;
        const SCROLL_BATCH = 50;
        let allMessages = [];
        // --- Client identity -------------------------------------------------------
        // Each browser generates its own Ed25519 keypair on first use (WebCrypto).
        // The resulting did:key is stable across sessions on this device and never
        // contains the gateway URL — it works the same over the internet.
        const _B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
        function _b58enc(u8) {
            let n = 0n;
            for (const b of u8) n = (n << 8n) | BigInt(b);
            let s = "";
            while (n > 0n) { const r = n % 58n; s = _B58[Number(r)] + s; n = (n - r) / 58n; }
            let leading = 0; for (const b of u8) { if (b !== 0) break; leading++; }
            return "1".repeat(leading) + s;
        }
        function _pubBytesToDid(pub32) {
            const mc = new Uint8Array(34); mc[0] = 0xed; mc[1] = 0x01; mc.set(pub32, 2);
            return "did:key:z" + _b58enc(mc);
        }
        // Returns the last 5 chars of a DID (or any identity string) as a short, unique suffix.
        // For did:key, these chars are from the base58btc-encoded key — cryptographically unique.
        function didSuffix(id) {
            if (!id || id.length < 5) return "";
            return id.slice(-5);
        }
        function escHtml(str) {
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }
        function formatTimestamp(ts) {
            if (!ts) return '';
            const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
            if (isNaN(d)) return String(ts);
            return d.toLocaleString();
        }
        let clientDid = localStorage.getItem("proxion_identity_did") || null;
        // R9.1: live handle to the non-extractable private CryptoKey — never serialised
        let _identityPrivKey = null;

        // R9.1.1: IndexedDB helpers for structured-clone-safe CryptoKey storage
        function _openIdentityDb() {
            return new Promise((resolve, reject) => {
                const req = indexedDB.open("proxion-identity", 1);
                req.onupgradeneeded = e => e.target.result.createObjectStore("keys");
                req.onsuccess = e => resolve(e.target.result);
                req.onerror = e => reject(e.target.error);
            });
        }
        function _idbGet(db, key) {
            return new Promise((resolve, reject) => {
                const req = db.transaction("keys", "readonly").objectStore("keys").get(key);
                req.onsuccess = e => resolve(e.target.result);
                req.onerror = e => reject(e.target.error);
            });
        }
        function _idbPut(db, key, value) {
            return new Promise((resolve, reject) => {
                const req = db.transaction("keys", "readwrite").objectStore("keys").put(value, key);
                req.onsuccess = () => resolve();
                req.onerror = e => reject(e.target.error);
            });
        }

        // R9.1.2: Load from IDB, migrate from localStorage, or generate fresh
        async function generateOrLoadIdentity() {
            if (clientDid && _identityPrivKey) return;
            try {
                const db = await _openIdentityDb();
                const record = await _idbGet(db, "identity");
                if (record && record.privateKey && record.did) {
                    // R9.1.2a: Already in IDB — load and done
                    _identityPrivKey = record.privateKey;
                    clientDid = record.did;
                    localStorage.setItem("proxion_identity_did", clientDid);
                    return;
                }

                // R9.1.2b: Migration — existing user has key in localStorage
                const legacyPrivJwkStr = localStorage.getItem("proxion_identity_priv_jwk");
                const legacyPubJwkStr = localStorage.getItem("proxion_identity_pub_jwk");
                if (legacyPrivJwkStr && legacyPubJwkStr) {
                    try {
                        const privJwk = JSON.parse(legacyPrivJwkStr);
                        const pubJwk = JSON.parse(legacyPubJwkStr);
                        // Import private as non-extractable — the JWK value itself is wiped below
                        const privKey = await crypto.subtle.importKey(
                            "jwk", privJwk, { name: "Ed25519" }, false, ["sign"]);
                        const pubKey = await crypto.subtle.importKey(
                            "jwk", pubJwk, { name: "Ed25519" }, true, ["verify"]);
                        const pubB64 = pubJwk.x.replace(/-/g, "+").replace(/_/g, "/");
                        const pubBytes = Uint8Array.from(atob(pubB64), c => c.charCodeAt(0));
                        clientDid = clientDid || _pubBytesToDid(pubBytes);
                        await _idbPut(db, "identity", { privateKey: privKey, publicKey: pubKey, did: clientDid });
                        _identityPrivKey = privKey;
                        // Remove the plaintext private key from localStorage
                        localStorage.removeItem("proxion_identity_priv_jwk");
                        localStorage.setItem("proxion_identity_did", clientDid);
                        return;
                    } catch (migErr) {
                        console.warn("[Proxion] Key migration failed, generating new identity:", migErr);
                    }
                }

                // R9.1.2c: Fresh generation — private key is non-extractable
                const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
                // Public key still extractable so we can compute the DID
                const pubKey = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
                // generateKey doesn't let us split extractability per key in one call; use importKey trick:
                // Generate extractable pair, use public key for DID, re-import private as non-extractable
                const kpExtractable = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
                const pubJwk = await crypto.subtle.exportKey("jwk", kpExtractable.publicKey);
                const privJwkTemp = await crypto.subtle.exportKey("jwk", kpExtractable.privateKey);
                const privKeyFinal = await crypto.subtle.importKey(
                    "jwk", privJwkTemp, { name: "Ed25519" }, false, ["sign"]);
                const pubKeyFinal = await crypto.subtle.importKey(
                    "jwk", pubJwk, { name: "Ed25519" }, true, ["verify"]);
                const pubB64 = pubJwk.x.replace(/-/g, "+").replace(/_/g, "/");
                const pubBytes = Uint8Array.from(atob(pubB64), c => c.charCodeAt(0));
                clientDid = _pubBytesToDid(pubBytes);
                await _idbPut(db, "identity", { privateKey: privKeyFinal, publicKey: pubKeyFinal, did: clientDid });
                _identityPrivKey = privKeyFinal;
                localStorage.setItem("proxion_identity_did", clientDid);
                localStorage.setItem("proxion_identity_pub_jwk", JSON.stringify(pubJwk));
                // privJwkTemp was only used during key generation — clear it
                Object.keys(privJwkTemp).forEach(k => { privJwkTemp[k] = null; });
            } catch (err) {
                // Fallback: random persistent ID if WebCrypto Ed25519 unavailable
                console.warn("[Proxion] WebCrypto Ed25519 unavailable, using random ID:", err);
                const rand = crypto.getRandomValues(new Uint8Array(32));
                const mc = new Uint8Array(34); mc[0] = 0xed; mc[1] = 0x01; mc.set(rand, 2);
                clientDid = "did:key:z" + _b58enc(mc);
                localStorage.setItem("proxion_identity_did", clientDid);
            }
        }

        // R9.1.3: Sign auth challenge using in-memory CryptoKey — no localStorage access
        async function _respondToAuthChallenge(nonce) {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;
            try {
                if (!_identityPrivKey) await generateOrLoadIdentity();
                if (!_identityPrivKey) return; // fallback DID path — no key available
                const sig = await crypto.subtle.sign('Ed25519', _identityPrivKey,
                    new TextEncoder().encode(nonce));
                const sigB64 = btoa(String.fromCharCode(...new Uint8Array(sig)))
                    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
                socket.send(JSON.stringify({ cmd: 'auth_response', nonce, signature: sigB64 }));
            } catch (err) {
                console.warn('[Proxion] Auth challenge signing failed:', err);
            }
        }
        // ---------------------------------------------------------------------------

        let socket = null;
        let activeView = null;
        let currentCall = null;
        let localStream = null;
        let pc = null;
        let typingUsers = {}; // webid -> room/dm id
        let unreadCounts = {}; // id -> count
        let messageReactions = {}; // messageId -> { emoji: [webid] }
        let lastEmojiMsgId = null;
        let replyingTo = null; // { id, name, content }
        let messageMap = {}; // id -> msg object
        let currentRoomMembers = []; // [{ webid, display_name, status }] for @mention autocomplete
        let mutedThreads = new Set(JSON.parse(localStorage.getItem("proxion_muted_threads") || "[]"));
        let currentDisappearMs = 0; // R11.1.3: active timer for current thread
        let _fingerprintBarDid = null; // R11.2.2: DID shown in fingerprint bar
        let selfWebId = clientDid; // set from localStorage immediately; updated after generateOrLoadIdentity
        let selfPubHex = null;
        let turnUrl = null;
        let turnSecret = null;
        let _turnIceServer = null; // pre-computed from gateway 'registered' event
        let editingMsgId = null;
        let currentCallSessionId = null;
        let isMuted = false;
        let _pendingCandidates = [];
        let _remoteDescSet = false;
        let _callTimeoutId = null;
        const CALL_TIMEOUT_MS = 30000;
        const CallState = Object.freeze({
            IDLE: 'idle',
            CALLING: 'calling',
            RINGING: 'ringing',
            CONNECTED: 'connected',
            ENDING: 'ending',
        });
        let _callState = CallState.IDLE;
        let callStartTime = null;
        let callTimerInterval = null;
        let roomInviteUrls = {}; // room_id -> invite_url
        let roomCreatorOf = new Set(); // room_ids this user owns
        let ringOscillator = null;
        let _lastRenderedDate = null;   // for date dividers
        let _scrollBottomUnread = 0;    // count of messages arrived while scrolled up
        let _reconnectTimer = null;     // for "Server unreachable" banner escalation
        let _reconnectDelay = 3000;    // exponential backoff; resets to 3000 on successful connect
        let _pendingOnConnect = [];    // commands queued while socket is still connecting
        let userPresence = {};  // webid -> { status: "online"|"away"|"busy"|"offline", updated_at: iso_timestamp }
        let dmLastMessages = {};       // thread_id -> { snippet, timestamp }
        let roomLastMessages = {};     // room_id -> { snippet, senderName, timestamp }
        let localDmPeers = {};         // thread_id -> { display_name, peer_webid }
        let peerDidToCertId = {};      // peer DID -> certificate_id (populated by renderContacts)
        let _threadNames = {};         // thread_id -> display_name (for tray unread menu)
        let _pendingFriendRequest = false;
        let hiddenDms = new Set(JSON.parse(localStorage.getItem("proxion_hidden_dms") || "[]"));
        localStorage.removeItem("theme"); // stale key from removed light-mode feature
        const _local_rooms = {};
        const _podReadLastFetch = {};
        const POD_READ_DEBOUNCE_MS = 30000;

        const _SVG_BELL = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0"/></svg>';
        const _SVG_BELL_SLASH = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M9.143 17.082a24.248 24.248 0 0 0 3.844.148m-3.844-.148a23.856 23.856 0 0 1-5.455-1.31 8.964 8.964 0 0 0 2.3-5.542m3.155 6.852a3 3 0 0 0 5.667 1.97m1.965-2.277L21 21m-4.225-4.225a23.81 23.81 0 0 0 3.536-1.003A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6.53 6.53m10.245 10.245L6.53 6.53M3 3l3.53 3.53"/></svg>';
        let soundEnabled = localStorage.getItem("soundEnabled") === "true";
        let _profileCardActive = null;  // webid of user whose profile is currently shown
        const soundBtn = document.getElementById("sound-toggle");
        soundBtn.innerHTML = soundEnabled ? _SVG_BELL : _SVG_BELL_SLASH;
        soundBtn.onclick = () => {
            soundEnabled = !soundEnabled;
            localStorage.setItem("soundEnabled", soundEnabled);
            soundBtn.innerHTML = soundEnabled ? _SVG_BELL : _SVG_BELL_SLASH;
        };

        function playNotificationSound() {
            if (!soundEnabled) return;
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

        // â"€â"€ Push notifications â"€â"€
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
            if (!soundEnabled) return;
            const n = new Notification(safeTitle, {
                body: safeBody,
                tag: threadId,
            });
            n.onclick = () => { window.focus(); n.close(); };
            setTimeout(() => n.close(), 6000);
        }

        // â"€â"€ Room members panel â"€â"€
        function toggleMembersPanel() {
            const panel = document.getElementById("members-panel");
            const isMobile = window.innerWidth <= 768;
            if (isMobile) {
                const isOpen = panel.classList.contains("mobile-open");
                panel.classList.toggle("mobile-open", !isOpen);
                if (!isOpen && activeView) requestRoomMembers(activeView.id);
            } else {
                const isShown = panel.style.display === "block";
                panel.style.display = isShown ? "none" : "block";
                if (!isShown && activeView) requestRoomMembers(activeView.id);
            }
        }

        function requestRoomMembers(roomId) {
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ cmd: "get_room_members", room_id: roomId }));
            }
        }

        function memberHtml(m) {
            const color = webidColor(m.webid);
            const displayName = m.display_name || m.webid || "?";
            const initial = escHtml(displayName[0].toUpperCase());
            const presenceClass = m.status === "online" ? "online" : m.status === "away" ? "away" : m.status === "busy" ? "busy" : "";
            const fedBadge = m.federated
                ? `<span title="Federated member (${escHtml(m.gateway || 'remote gateway')})" style="font-size:0.65em;color:#64748b;margin-left:4px;vertical-align:middle;">&#x1F517;</span>`
                : "";
            return `<div class="member-item" data-msg-action="profile" data-webid="${escHtml(m.webid)}" data-name="${escHtml(displayName)}">
                <div style="position:relative;display:inline-block;margin-right:8px;">
                    <div class="avatar placeholder" style="background:${color};width:28px;height:28px;line-height:28px;font-size:12px;font-weight:bold;text-align:center;">${initial}</div>
                    <div class="avatar-presence ${presenceClass}" title="${escHtml(m.status || '')}"></div>
                </div>
                <span>${escHtml(m.display_name || m.webid.slice(0, 12))}${fedBadge}</span>
            </div>`;
        }

        function renderMembersPanel(members) {
            const list = document.getElementById("members-list");
            const online = members.filter(m => m.status === "online");
            const offline = members.filter(m => m.status !== "online");
            list.innerHTML = "";
            if (online.length) {
                list.innerHTML += `<div class="members-section-header">Online — ${online.length}</div>`;
                online.forEach(m => list.innerHTML += memberHtml(m));
            }
            if (offline.length) {
                list.innerHTML += `<div class="members-section-header">Offline — ${offline.length}</div>`;
                offline.forEach(m => list.innerHTML += memberHtml(m));
            }
        }

        // â"€â"€ DM sidebar with last message preview + recency sort â"€â"€
        function renderDmSidebar() {
            const list = document.getElementById("dm-list");
            const entries = Object.entries(localDmPeers);
            entries.sort(([idA], [idB]) => {
                const tA = dmLastMessages[idA]?.timestamp || "";
                const tB = dmLastMessages[idB]?.timestamp || "";
                return tB.localeCompare(tA);
            });
            list.innerHTML = "";
            let dmCount = 0;
            entries.forEach(([id, peer]) => {
                if (hiddenDms.has(id)) return;
                dmCount++;
                const name = peer.display_name || (peer.peer_webid || id).slice(0, 12);
                const last = dmLastMessages[id];
                const li = document.createElement("li");
                li.id = `nav-${id}`;
                li.className = "dm-item" + (activeView && activeView.id === id ? " active" : "");
                const body = document.createElement("div");
                body.className = "dm-item-body";
                const ts = last ? timeAgo(last.timestamp) : "";
                body.innerHTML = `<div class="dm-item-name">${escHtml(name)}${ts ? `<span style="color:#64748b;font-size:0.75em;float:right;margin-left:4px">${ts}</span>` : ""}</div>
                    ${last ? `<div class="dm-item-preview">${last.snippet.replace(/</g,"&lt;")}</div>` : ""}`;
                const closeBtn = document.createElement("button");
                closeBtn.className = "dm-close-btn";
                closeBtn.innerText = "×";
                closeBtn.title = "Hide this DM";
                closeBtn.onclick = (e) => { e.stopPropagation(); hideDm(id); };
                li.appendChild(body);
                li.appendChild(closeBtn);
                li.onclick = () => openLocalDmThread(id, name, peer.peer_webid);
                li.addEventListener("contextmenu", e => openSidebarCtx(e, id));
                // Mute icon
                const muteIcon = document.createElement("span");
                muteIcon.className = "mute-icon";
                muteIcon.title = "Muted";
                muteIcon.style.cssText = `display:${mutedThreads.has(id) ? "" : "none"};font-size:0.75em;color:#64748b;margin-left:4px;flex-shrink:0;`;
                muteIcon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9.143 17.082a24.248 24.248 0 0 0 3.844.148m-3.844-.148a23.856 23.856 0 0 1-5.455-1.31 8.964 8.964 0 0 0 2.3-5.542m3.155 6.852a3 3 0 0 0 5.667 1.97m1.965-2.277L21 21m-4.225-4.225a23.81 23.81 0 0 0 3.536-1.003 8.967 8.967 0 0 1-2.312-6.022V9A6 6 0 0 0 9.239 3.477L3 3m6.239.477A5.965 5.965 0 0 0 6 9v.75a8.966 8.966 0 0 1-2.312 6.022"/></svg>';
                li.appendChild(muteIcon);
                list.appendChild(li);
                updateSidebarBadge(id);
            });
            if (!dmCount) {
                const hint = document.createElement("li");
                hint.style.cssText = "padding:6px 10px;color:#475569;font-size:0.78em;cursor:default;pointer-events:none;";
                hint.textContent = "Add a contact to start a DM";
                list.appendChild(hint);
            }
        }

        function hideEmptyState() {
            const el = document.getElementById("empty-state");
            if (el) el.style.display = "none";
        }
        function showEmptyState() {
            const el = document.getElementById("empty-state");
            if (el) {
                el.style.display = "flex";
                const addBtn = document.getElementById("empty-add-contact-btn");
                if (addBtn) addBtn.style.display = "";
            }
        }

        function _updateE2EStatus(peerId) {
            const el = document.getElementById('dm-e2e-status');
            if (!el) return;
            const btn = document.getElementById('dm-e2e-verify-btn');
            if (peerId && isE2EEnabled(peerId)) {
                const verified = localStorage.getItem('proxion_e2e_verified_' + peerId) === '1';
                el.innerHTML = verified ? '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25z"/></svg> E2E' : '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M13.5 10.5V6.75a4.5 4.5 0 1 1 9 0v3.75M3.75 21.75h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H3.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25z"/></svg> E2E';
                el.title = verified ? 'End-to-end encrypted (verified)' : 'End-to-end encrypted (tap Verify to confirm identity)';
                el.style.display = 'inline';
                if (btn) btn.style.display = 'inline';
            } else if (peerId && e2eSupported) {
                el.textContent = 'No E2E';
                el.title = 'E2E key not yet exchanged — send a message first';
                el.style.display = 'inline';
                if (btn) btn.style.display = 'none';
            } else {
                el.style.display = 'none';
                if (btn) btn.style.display = 'none';
            }
        }

        async function _updateIdentityFingerprint(peerDid) {
            const bar = document.getElementById("fingerprint-bar");
            const wordsEl = document.getElementById("fingerprint-words");
            const verifyBtn = document.getElementById("fingerprint-verify-btn");
            if (!bar || !wordsEl || !verifyBtn) return;
            if (!peerDid || !peerDid.startsWith("did:key:")) {
                bar.style.display = "none";
                _fingerprintBarDid = null;
                return;
            }
            _fingerprintBarDid = peerDid;
            bar.style.display = "flex";
            wordsEl.textContent = "loading…";
            try {
                const resp = await fetch(`/fingerprint/${encodeURIComponent(peerDid)}`);
                if (!resp.ok) { bar.style.display = "none"; return; }
                const data = await resp.json();
                const words = (data.safety_words || []);
                wordsEl.textContent = words.slice(0,3).join(" ") + "  " + words.slice(3).join(" ");
                const verified = localStorage.getItem("proxion_verified_" + peerDid) === "1";
                if (verified) {
                    verifyBtn.textContent = "✓ Verified";
                    verifyBtn.style.background = "#134e26";
                    verifyBtn.style.color = "#4ade80";
                    verifyBtn.disabled = true;
                } else {
                    verifyBtn.textContent = "Mark as verified";
                    verifyBtn.style.background = "#1e293b";
                    verifyBtn.style.color = "#94a3b8";
                    verifyBtn.disabled = false;
                }
            } catch (_) {
                bar.style.display = "none";
            }
        }

        async function _openVerifyModal(peerId) {
            const myPub   = myX25519PubB64u();
            const theirPub = localStorage.getItem('proxion_e2e_peer_pub_' + peerId);
            if (!myPub || !theirPub) return;

            const sn = await safetyNumber(myPub, theirPub);

            const shorten = s => s.slice(0, 12) + '…' + s.slice(-4);
            const modal = document.getElementById('e2e-verify-modal');
            document.getElementById('e2e-modal-my-key').textContent    = shorten(myPub);
            document.getElementById('e2e-modal-their-key').textContent  = shorten(theirPub);
            document.getElementById('e2e-modal-safety-number').textContent = sn;
            document.getElementById('e2e-modal-current-peer').value    = peerId;
            if (modal) modal.style.display = 'flex';
        }

        function openLocalDmThread(id, name, peerWebid) {
            hideEmptyState();
            activeView = { type: "local_dm", id: id, name: name, local: true, peerWebid: peerWebid };
            document.getElementById("chat-header-name").innerText = "@ " + name;
            _updateE2EStatus(peerWebid);
            _updateIdentityFingerprint(peerWebid);
            document.getElementById("message-feed").innerHTML = "";
            _lastRenderedDate = null; messageMap = {}; allMessages = [];
            currentRoomMembers = [];
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
                _sendUpdateLastRead(id);
            }
            // Pod: persist read state
            const _lastMsgForRead = allMessages.filter(m => m.thread_id === id).at(-1);
            if (_lastMsgForRead) podWriteReadState(id, _lastMsgForRead.message_id).catch(() => {});
            loadRoomHistory(id);
            if (window.innerWidth <= 768) toggleSidebar();
        }

        function hideDm(threadId) {
            hiddenDms.add(threadId);
            localStorage.setItem("proxion_hidden_dms", JSON.stringify([...hiddenDms]));
            renderDmSidebar();
            if (activeView && activeView.id === threadId) {
                activeView = null;
                showEmptyState();
                document.getElementById("message-feed").innerHTML = '<div class="system-msg">DM hidden. It will reappear when you receive a new message.</div>';
            }
        }

        // â"€â"€ Leave / delete room â"€â"€
        function leaveRoom() {
            if (!activeView || activeView.type !== "local_room") return;
            const isOwner = roomCreatorOf.has(activeView.id);
            const msg = isOwner
                ? "Leave this room? As the owner, ownership will be transferred to another member, or the room will be deleted if you're the last member."
                : "Leave this room? You can rejoin with the invite link.";
            showConfirm(msg, () => {
                socket.send(JSON.stringify({ cmd: "leave_local_room", room_id: activeView.id }));
            });
        }

        function deleteRoom() {
            if (!activeView || !roomCreatorOf.has(activeView.id)) return;
            showConfirm("Delete this room permanently? All messages and history will be lost for all members.", () => {
                socket.send(JSON.stringify({ cmd: "delete_room", room_id: activeView.id }));
            });
        }

        function transferOwnership(roomId, toDid) {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;
            socket.send(JSON.stringify({ cmd: "transfer_ownership", room_id: roomId, to_did: toDid }));
        }

        // Settings modal
        document.getElementById("settings-btn").onclick = () => {
            document.getElementById("settings-gw-url").value =
                localStorage.getItem("proxion_gateway_url") || "ws://127.0.0.1:7474";
            document.getElementById("settings-display-name").value =
                localStorage.getItem("proxion_display_name") || "";
            document.getElementById("settings-status-message").value =
                localStorage.getItem("proxion_status_message") || "";
            const _myDid = clientDid || localStorage.getItem("proxion_identity_did") || "";
            const _myName = localStorage.getItem("proxion_display_name") || "";
            const _mySuffix = didSuffix(_myDid);
            document.getElementById("settings-did").innerHTML = _myDid
                ? `<span style="color:#f1f5f9">${_myName || "(no name set)"}</span><span style="color:#475569;margin-left:4px;">Â·${_mySuffix}</span><br><span style="font-size:0.85em;color:#64748b;">${_myDid}</span>`
                : "(generating…)";
            document.getElementById("settings-proxion-address").textContent =
                localStorage.getItem("proxion_my_address") || "(not connected)";
            // Fetch live status from gateway (will update the pre-populated state)
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({cmd: "get_my_address"}));
                socket.send(JSON.stringify({cmd: "pod_status"}));
                socket.send(JSON.stringify({cmd: "list_sessions"}));
                socket.send(JSON.stringify({cmd: "list_devices"}));
            }
            document.getElementById("settings-modal").style.display = "flex";
            // R33: Fetch connectivity + health for settings federation panel
            Promise.all([fetch('/connectivity').then(r=>r.json()), fetch('/health').then(r=>r.json())])
              .then(([c, h]) => {
                const el = document.getElementById('settings-federation-status');
                if (!el) return;
                const tick = ok => ok ? '<span style="color:#4ade80">&#x2713;</span>' : '<span style="color:#f87171">&#x2717;</span>';
                const reachable = c.public_url_set || c.relay_capable;
                const reachHow = reachable
                    ? (c.upnp_mapped ? ' <span style="color:#64748b;font-size:0.85em;">(via UPnP)</span>' : ' <span style="color:#64748b;font-size:0.85em;">(manual)</span>')
                    : ` <span style="color:#fbbf24">not reachable — <a href="#" id="fix-conn-link" style="color:#fbbf24;text-decoration:underline;">fix this</a></span>`;
                el.innerHTML = [
                    `${tick(reachable)} Internet reachable:${reachHow}`,
                    `${tick(h.turn_configured)} TURN: ${h.turn_configured ? 'configured' : '<span style="color:#fbbf24">not set</span>'}`,
                    `${tick(h.pod_available)} Solid Pod: ${h.pod_available ? 'connected' : 'offline'}`,
                ].join('<br>');
                document.getElementById('fix-conn-link')?.addEventListener('click', e => {
                    e.preventDefault();
                    sessionStorage.removeItem('proxion_nat_dismissed');
                    _showNatWarning();
                });
              }).catch(() => {});
            // R16.4.2: restore pod connected/disconnected state from localStorage (live pod_status will update)
            {
                const _podOk = localStorage.getItem('proxion_pod_connected') === '1';
                _updateSettingsPodDot(_podOk ? 'connected' : 'none');
                const _scd = document.getElementById('settings-pod-connected');
                const _sdd = document.getElementById('settings-pod-disconnected');
                if (_scd) _scd.style.display = _podOk ? 'block' : 'none';
                if (_sdd) _sdd.style.display = _podOk ? 'none' : 'block';
                const _swe = document.getElementById('settings-pod-webid');
                if (_swe) _swe.textContent = localStorage.getItem('proxion_pod_webid') || '';
            }
            // R18.1.3 + R18.3.3: show Tauri-only section when running as desktop app
            if (window.__TAURI__?.invoke) {
                const tauriSection = document.getElementById('settings-tauri-section');
                if (tauriSection) tauriSection.style.display = '';
                // Load autostart state
                window.__TAURI__.invoke('plugin:autostart|is_enabled').then(enabled => {
                    const toggle = document.getElementById('settings-autostart-toggle');
                    if (toggle) toggle.checked = !!enabled;
                }).catch(() => {});
                // Show app version from gateway well-known
                fetch('http://127.0.0.1:8080/.well-known/proxion')
                    .then(r => r.json())
                    .then(d => {
                        const el = document.getElementById('settings-app-version');
                        if (el && d.gateway_version) el.textContent = d.gateway_version;
                    })
                    .catch(() => {});
            }
        };
        document.getElementById("settings-save-btn").onclick = () => {
            const newGwUrl = document.getElementById("settings-gw-url").value.trim();
            const gwUrlChanged = newGwUrl && newGwUrl !== localStorage.getItem("proxion_gateway_url");
            if (newGwUrl) localStorage.setItem("proxion_gateway_url", newGwUrl);
            const displayName = document.getElementById("settings-display-name").value.trim();
            if (displayName) {
                localStorage.setItem("proxion_display_name", displayName);
                document.getElementById("username").innerText = displayName;
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({cmd: "set_identity", display_name: displayName}));
                }
                podWriteProfile({ displayName }).catch(() => {});
            }
            const statusMessage = document.getElementById("settings-status-message").value.trim();
            localStorage.setItem("proxion_status_message", statusMessage);
            if (socket && socket.readyState === WebSocket.OPEN && statusMessage) {
                socket.send(JSON.stringify({cmd: "set_presence", status: "online", status_message: statusMessage}));
            }
            document.getElementById("settings-modal").style.display = "none";
            if (gwUrlChanged) { if (socket) socket.close(); location.reload(); }
        };

        document.getElementById("add-peer-btn").onclick = () => {
            document.getElementById("add-peer-input").value = "";
            document.getElementById("add-peer-error").textContent = "";
            document.getElementById("add-peer-modal").style.display = "flex";
            setTimeout(() => document.getElementById("add-peer-input").focus(), 50);
        };

        function copyMyAddress() {
            const addr = localStorage.getItem("proxion_my_address") || "";
            if (!addr) return;
            navigator.clipboard.writeText(addr).then(() => {
                showToast("Address copied!");
                const btn = document.getElementById("copy-addr-btn");
                if (btn) {
                    const orig = btn.textContent;
                    btn.textContent = "✓ Copied";
                    setTimeout(() => { btn.textContent = orig; }, 2000);
                }
            }).catch(() => { showCopyModal(addr); });
        }

        // R17.1: Render QR code into #my-qr container
        function renderMyQR(url) {
            if (!url || typeof QRCode === 'undefined') return;
            const container = document.getElementById('my-qr');
            if (!container) return;
            container.innerHTML = '';
            new QRCode(container, {
                text: url,
                width: 200,
                height: 200,
                colorDark: '#0f172a',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M,
            });
        }

        // R17.1: Open QR share panel anchored near the button
        function shareInviteLink() {
            const link = window.proxionInviteLink;
            if (!link) { showToast('No invite link available yet — wait for connection.'); return; }
            const panel = document.getElementById('qr-share-panel');
            if (!panel) return;
            // R17.4.5: encode proxion:// deep-link so scanning on a device with the app installed
            // opens it directly; fall back to HTTP short/invite URL on devices without the app.
            const addr = window.proxionAddress || localStorage.getItem('proxion_my_address');
            const qrUrl = addr
                ? `proxion://invite?from=${encodeURIComponent(addr)}`
                : (window.proxionShortInviteUrl || link);
            renderMyQR(qrUrl);
            // position near top-right
            panel.style.top = '64px';
            panel.style.right = '12px';
            panel.style.left = 'auto';
            panel.style.display = 'block';
        }

        function updateMyAddressBar(addr) {
            const bar = document.getElementById("my-address-bar");
            const short = document.getElementById("my-address-short");
            const full = document.getElementById("settings-proxion-address");
            if (full) full.textContent = addr || "";
            if (!addr || !bar || !short) return;
            const atIdx = addr.lastIndexOf("@");
            const truncDid = addr.slice(0, 20) + "…";
            const domain = atIdx > -1 ? addr.slice(atIdx) : "";
            short.textContent = truncDid + domain;
            short.title = addr;
            bar.style.display = "flex";
        }

        function renderPendingInvite(req) {
            const list = document.getElementById("friend-request-list");
            if (!list || document.getElementById("fri-" + req.invitation_id)) return;
            const fromShort = req.display_name || ((req.from_did || "unknown").slice(8, 22) + "…");
            const li = document.createElement("li");
            li.id = "fri-" + req.invitation_id;
            li.dataset.peerDid = req.from_did || "";
            li.style.cssText = "padding:6px 8px;background:#1e293b;border-radius:6px;margin:3px 0";
            li.innerHTML =
                `<div style="color:#e2e8f0;margin-bottom:4px">From <b>${escHtml(fromShort)}</b></div>` +
                `<div style="display:flex;gap:6px">` +
                `<button data-fr-action="accept" data-inv-id="${escHtml(req.invitation_id)}" ` +
                `style="background:#7c3aed;color:#fff;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.8em">Accept</button>` +
                `<button data-fr-action="dismiss" data-inv-id="${escHtml(req.invitation_id)}" ` +
                `style="background:#334155;color:#94a3b8;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.8em">Ignore</button>` +
                `</div>`;
            list.appendChild(li);
            refreshFriendRequestsBadge();
        }

        function acceptFriendRequest(invitationId) {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;
            socket.send(JSON.stringify({cmd: "accept_friend_request", invitation_id: invitationId}));
        }

        function refreshFriendRequestsBadge() {
            const section = document.getElementById("friend-requests-section");
            const list = document.getElementById("friend-request-list");
            if (section) section.style.display = list && list.children.length ? "" : "none";
        }

        // R16.4.2: update the pod status dot in the settings modal header
        function _updateSettingsPodDot(state) {
            const dot = document.getElementById('settings-pod-status-dot');
            if (!dot) return;
            if (state === 'connected') {
                dot.style.color = '#4ade80';
                dot.textContent = '● Pod connected';
            } else if (state === 'unreachable') {
                dot.style.color = '#fb923c';
                dot.textContent = '● Pod unreachable';
            } else {
                dot.style.color = '#64748b';
                dot.textContent = '● No pod';
            }
        }

        function _syncTrayUnread() {
            if (!window.__TAURI__?.invoke) return;
            const contacts = Object.entries(unreadCounts)
                .filter(([, c]) => c > 0)
                .map(([id, count]) => ({ name: _threadNames[id] || id.slice(0, 12), count, thread_id: id }))
                .sort((a, b) => b.count - a.count);
            window.__TAURI__.invoke('update_tray_unread', { contacts }).catch(() => {});
        }

        function renderContacts(contacts) {
            const list = document.getElementById("contacts-list");
            const section = document.getElementById("contacts-section");
            if (!list || !section) return;
            list.innerHTML = "";
            if (!contacts || contacts.length === 0) { section.style.display = "none"; return; }
            section.style.display = "";
            hideEmptyState();
            peerDidToCertId = {};
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
            hideEmptyState();
            activeView = {
                type: "dm",
                id: contact.certificate_id,
                name: contact.display_name || (contact.peer_did || "").slice(8, 22) + "…",
                certId: contact.certificate_id,
                peerDid: contact.peer_did,
                peerWebid: contact.peer_did,
                local: false,
            };
            const header = document.getElementById("chat-header-name");
            if (header) header.textContent = activeView.name;
            _updateE2EStatus(contact.peer_did);
            _updateIdentityFingerprint(contact.peer_did);
            // Clear feed and reset message state
            const feed = document.getElementById("message-feed");
            if (feed) feed.innerHTML = "";
            _lastRenderedDate = null;
            messageMap = {};
            allMessages = [];
            // Highlight sidebar item if present
            document.querySelectorAll("nav li").forEach(el => el.classList.remove("active"));
            const navEl = document.getElementById("nav-" + contact.certificate_id);
            if (navEl) navEl.classList.add("active");
            // Fetch history from gateway
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({cmd: "read_dm", cert_id: contact.certificate_id}));
                socket.send(JSON.stringify({cmd: "mark_read", thread_id: contact.certificate_id}));
            }
            // Clear unread badge
            unreadCounts[contact.certificate_id] = 0;
            updateSidebarBadge(contact.certificate_id);
        }

        // ---- Room creation ----
        document.getElementById("join-room-btn").onclick = () => {
            document.getElementById("join-room-input").value = "";
            document.getElementById("join-room-error").textContent = "";
            document.getElementById("join-room-modal").style.display = "flex";
            setTimeout(() => document.getElementById("join-room-input").focus(), 50);
        };

        document.getElementById("create-room-btn").onclick = () => {
            // Reset modal to form state
            document.getElementById("room-create-form").style.display = "";
            document.getElementById("room-invite-result").style.display = "none";
            document.getElementById("room-name-input").value = "";
            document.getElementById("room-history-toggle").checked = false;
            document.getElementById("room-create-modal").style.display = "flex";
            setTimeout(() => document.getElementById("room-name-input").focus(), 50);
        };

        document.getElementById("room-create-submit").onclick = () => {
            const name = document.getElementById("room-name-input").value.trim();
            if (!name) { document.getElementById("room-name-input").focus(); return; }
            const historyMode = document.getElementById("room-history-toggle").checked ? "all" : "none";
            socketSendOrQueue({cmd: "chat_room_create", name: name, history_mode: historyMode});
        };

        document.getElementById("room-name-input").onkeydown = (e) => {
            if (e.key === "Enter") document.getElementById("room-create-submit").click();
        };

        function copyRoomInviteFromModal() {
            const url = document.getElementById("room-invite-url").textContent;
            navigator.clipboard.writeText(url).then(() => {
                document.getElementById("room-invite-url").textContent = "Copied!";
                setTimeout(() => { document.getElementById("room-invite-url").textContent = url; }, 1500);
            }).catch(() => {
                showCopyModal(url);
            });
        }

        function copyRoomInvite() {
            if (!activeView || !roomInviteUrls[activeView.id]) return;
            const url = roomInviteUrls[activeView.id];
            // Extract the raw code from the URL (?join=CODE) so users can share just the code
            const codeMatch = url.match(/[?&]join=([^&]+)/);
            const code = codeMatch ? codeMatch[1] : url;
            const urlEl = document.getElementById("invite-modal-url");
            const codeEl = document.getElementById("invite-modal-code");
            if (urlEl) urlEl.textContent = url;
            if (codeEl) codeEl.textContent = code;
            document.getElementById("room-invite-modal").style.display = "flex";
        }

        function _copyInviteText(text, btn) {
            navigator.clipboard.writeText(text).then(() => {
                const orig = btn.textContent;
                btn.textContent = "Copied!";
                setTimeout(() => { btn.textContent = orig; }, 1500);
            }).catch(() => { showCopyModal(text); });
        }

        attachListener('#invite-modal-copy-url', 'click', () => {
            const url = document.getElementById("invite-modal-url")?.textContent || "";
            _copyInviteText(url, document.getElementById("invite-modal-copy-url"));
        });
        attachListener('#invite-modal-copy-code', 'click', () => {
            const code = document.getElementById("invite-modal-code")?.textContent || "";
            _copyInviteText(code, document.getElementById("invite-modal-copy-code"));
        });
        attachListener('#invite-modal-close', 'click', () => {
            document.getElementById("room-invite-modal").style.display = "none";
        });

        let _membersRoomId = null;
        function showRoomMembers(roomId) {
            _membersRoomId = roomId;
            document.getElementById("room-members-list").innerHTML = "<p style='color:#94a3b8'>Loading...</p>";
            document.getElementById("room-members-modal").style.display = "flex";
            if (socket) socket.send(JSON.stringify({cmd: "get_room_members", room_id: roomId}));
        }
        function kickMember(roomId, webid) {
            showConfirm("Kick this member?", () => {
                if (socket) socket.send(JSON.stringify({cmd: "kick_member", room_id: roomId, webid: webid}));
            });
        }

        function updateRoomPreview(roomId) {
            const li = document.getElementById(`nav-${roomId}`);
            if (!li) return;
            const preview = roomLastMessages[roomId];
            const previewEl = li.querySelector(".room-item-preview");
            if (previewEl && preview) {
                previewEl.textContent = (preview.senderName ? preview.senderName + ": " : "") + preview.snippet;
            }
        }

        function addRoomToSidebar(roomId, name, inviteUrl) {
            if (inviteUrl) roomInviteUrls[roomId] = inviteUrl;
            if (name) _threadNames[roomId] = name;
            if (document.getElementById(`nav-${roomId}`)) return; // already added
            const hint = document.getElementById("room-list-empty-hint");
            if (hint) hint.remove();
            const list = document.getElementById("room-list");
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
                        style="background:transparent;border:none;color:#64748b;cursor:pointer;padding:2px 4px;font-size:0.85em;flex-shrink:0;"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z"/></svg></button>`;
            li.onclick = () => {
                hideEmptyState();
                activeView = {type: "local_room", id: roomId, name: name, local: true};
                document.getElementById("chat-header-name").innerText = "# " + name;
                _updateIdentityFingerprint(null); // hide fingerprint bar in room views
                document.getElementById("message-feed").innerHTML = "";
                _lastRenderedDate = null;
                messageMap = {};
                allMessages = [];
                currentRoomMembers = [];
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
                    _sendUpdateLastRead(roomId);
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

        // Auto-join from URL ?join=CODE
        (function checkAutoJoin() {
            const params = new URLSearchParams(window.location.search);
            const code = params.get("join");
            if (code) {
                // Wait for socket to be open, then join
                const tryJoin = () => {
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({cmd: "join_room", code: code}));
                        // Clean URL without reload
                        history.replaceState(null, "", window.location.pathname);
                    } else {
                        setTimeout(tryJoin, 300);
                    }
                };
                setTimeout(tryJoin, 500);
            }
        })();

        // Send payload now if socket is open; otherwise queue it and send on next onopen.
        // If socket is stuck in a closed/backoff state, kicks off a fresh connect immediately.
        function socketSendOrQueue(payload, { statusEl } = {}) {
            const rs = socket ? socket.readyState : -1;
            if (rs !== WebSocket.OPEN) console.warn("[Proxion] socketSendOrQueue: socket not open, readyState=", rs, "(0=CONNECTING 1=OPEN 2=CLOSING 3=CLOSED -1=null)");
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify(payload));
                return;
            }
            if (statusEl) { statusEl.textContent = "Connecting to gateway…"; statusEl.style.color = "#94a3b8"; }
            // If socket is closed (not just still connecting), restart immediately — don't wait
            // for the exponential-backoff timer which may be up to 60s.
            if (!socket || socket.readyState === WebSocket.CLOSED) {
                if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
                _reconnectDelay = 3000;
                connect();
            }
            // After 8s with no connection, nudge the user — but keep waiting (don't hard-fail).
            const nudgeTimer = setTimeout(() => {
                const stillQueued = _pendingOnConnect.some(p => p.nudgeTimer === nudgeTimer);
                if (stillQueued && statusEl) {
                    statusEl.innerHTML = 'Still connecting… <span style="color:#fbbf24">Is the gateway running?</span>';
                }
            }, 8000);
            _pendingOnConnect.push({ payload, statusEl, nudgeTimer });
        }

        function forceReconnect() {
            if (socket && socket.readyState === WebSocket.OPEN) return;
            if (_reconnectTimer) { clearInterval(_reconnectTimer); _reconnectTimer = null; }
            _reconnectDelay = 3000;
            const oldSocket = socket;
            socket = null; // disown before closing so its onclose is ignored
            if (oldSocket) { try { oldSocket.close(); } catch(e) {} }
            connect();
        }
        function connect() {
            // Each call captures its own ws reference so stale onclose/onopen events
            // from a superseded socket cannot overwrite state or schedule extra reconnects.
            const ws = new WebSocket(WS_URL);
            socket = ws;

            // If the port is silently filtered (Windows Firewall etc.) the socket hangs
            // in CONNECTING forever. Force-close after 8s so the error path runs.
            const _connectTimeout = setTimeout(() => {
                if (ws.readyState === WebSocket.CONNECTING) {
                    console.warn("[Proxion] Connect timeout — gateway unreachable at", WS_URL);
                    ws.close();
                }
            }, 8000);

            ws.onopen = async () => {
                if (socket !== ws) { ws.close(); return; } // superseded
                clearTimeout(_connectTimeout);
                // Ensure identity is always ready before we try to register.
                // generateOrLoadIdentity() is idempotent — if already loaded it returns instantly.
                await generateOrLoadIdentity();
                if (socket !== ws) return; // socket superseded while we were loading identity
                console.log("Connected to gateway");
                _reconnectDelay = 3000;
                document.querySelector(".dot").className = "dot online";
                const _connName = localStorage.getItem("proxion_display_name");
                document.getElementById("username").innerText = _connName || "Online";
                document.getElementById("conn-banner").style.display = "none";
                if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
                // Flush any commands that were queued while socket was connecting
                const pending = _pendingOnConnect.splice(0);
                pending.forEach(({ payload, statusEl, nudgeTimer }) => {
                    clearTimeout(nudgeTimer);
                    ws.send(JSON.stringify(payload));
                    if (statusEl) { statusEl.textContent = "Connecting…"; statusEl.style.color = "#94a3b8"; }
                });
                // Register with this client's own DID (always — every user has one)
                // Include x25519_pub so peers learn our E2E key when we reconnect
                // Include display_name so the gateway has it immediately (avoids a separate set_identity before auth)
                const _regPayload = {cmd: "register", did: clientDid};
                const _storedName = localStorage.getItem("proxion_display_name");
                if (_storedName) _regPayload.display_name = _storedName;
                const _e2ePub = myX25519PubB64u();
                if (_e2ePub) _regPayload.x25519_pub = _e2ePub;
                ws.send(JSON.stringify(_regPayload)); // clientDid always set after generateOrLoadIdentity()
                // All other init commands are deferred to the "registered" event handler so
                // they never race with the auth challenge-response cycle under require_auth mode.
                document.getElementById("message-feed").innerHTML += '<div class="system-msg">Connected to gateway.</div>';
            };

            ws.onmessage = (event) => {
                if (socket !== ws) return; // superseded
                const data = JSON.parse(event.data);
                _handleEventAsync(data);
            };

            ws.onerror = (err) => {
                console.error("Gateway WebSocket error — check that the gateway is running on", WS_URL, err);
            };

            ws.onclose = () => {
                clearTimeout(_connectTimeout);
                if (socket !== ws) return; // superseded — don't clobber state or schedule reconnect
                console.log("Disconnected from gateway");
                document.querySelector(".dot").className = "dot offline";
                const banner = document.getElementById("conn-banner");
                // First attempt: retry immediately. Subsequent attempts: exponential backoff.
                const retryMs = _reconnectDelay === 3000 ? 0 : _reconnectDelay;
                _reconnectDelay = Math.min(_reconnectDelay * 2, 30000);
                if (retryMs === 0) {
                    // Instant retry — don't flash "Offline" for a transient hiccup
                    document.getElementById("username").innerText = "Connecting…";
                    banner.style.display = "none";
                    setTimeout(connect, 0);
                } else {
                    document.getElementById("username").innerText = localStorage.getItem("proxion_display_name") ? "Offline" : "Gateway offline";
                    banner.textContent = `Reconnecting in ${Math.round(retryMs / 1000)}s\u2026`;
                    banner.style.display = "block";
                    let remaining = Math.round(retryMs / 1000);
                    _reconnectTimer = setInterval(() => {
                        remaining--;
                        if (remaining > 0) {
                            banner.textContent = `Reconnecting in ${remaining}s\u2026`;
                        } else {
                            clearInterval(_reconnectTimer);
                            _reconnectTimer = null;
                        }
                    }, 1000);
                    setTimeout(() => {
                        if (_reconnectTimer) { clearInterval(_reconnectTimer); _reconnectTimer = null; }
                        connect();
                    }, retryMs);
                }
            };
        }

        // Pre-processes events asynchronously (E2E decrypt) then delegates to handleEvent.
        async function _handleEventAsync(event) {
            // Cache peer's X25519 pub key from any DM event
            if (event.x25519_pub && event.from_webid) {
                cachePeerPub(event.from_webid, event.x25519_pub);
            }
            // Decrypt E2E messages before rendering
            if (event.type === "message" && event.e2e && event.nonce) {
                const peerId = event.from_webid;
                if (peerId && peerId !== selfWebId && peerId !== clientDid) {
                    try {
                        event.content = await ratchetDecrypt(
                            peerId, event.content, event.nonce,
                            event.msg_num ?? 0, event.ratchet_pub ?? null,
                            event.pn ?? 0);
                        event.e2e = false; // mark decrypted
                    } catch (err) {
                        event.content = err instanceof E2EDecryptError
                            ? '[could not decrypt]'
                            : '[decryption error]';
                        event.e2e = false;
                    }
                }
            }
            handleEvent(event);
        }

        function normalizeRelayThreadId(event) {
            if (event.source !== "relay") return event;
            if (event.cert_id) return { ...event, thread_id: event.cert_id };
            const mapped = peerDidToCertId[event.from_webid];
            if (mapped) return { ...event, thread_id: mapped };
            return event;
        }

        function handleEvent(event) {
            switch (event.type) {
                case "message": {
                    const msg = normalizeRelayThreadId(event);
                    const id = msg.thread_id;

                    // Auto-add unknown relay sender to DM sidebar
                    if (msg.source === "relay" && !document.getElementById("nav-" + id)) {
                        const label = msg.from_display_name || (msg.from_webid || "").slice(8, 22) + "…";
                        localDmPeers[id] = { display_name: label, peer_webid: msg.from_webid || id };
                        renderDmSidebar();
                    }

                    renderMessage(msg);

                    // Update unread badges + DM last-message preview
                    if (!activeView || activeView.id !== id) {
                        if (!mutedThreads.has(id)) {
                            unreadCounts[id] = (unreadCounts[id] || 0) + 1;
                            updateSidebarBadge(id);
                        }
                        // Check for @mention
                        const selfName = localStorage.getItem("proxion_display_name") || "";
                        const mentionsMe = (msg.mentions && selfWebId && msg.mentions.includes(selfWebId)) ||
                            (selfName && (msg.content || "").toLowerCase().includes("@" + selfName.toLowerCase()));
                        if (mentionsMe) {
                            playNotificationSound();
                        }
                        const sender = msg.from_display_name || (msg.from_webid || "").slice(0, 12);
                        if (!mutedThreads.has(id)) {
                            showOsNotification(`${sender}`, msg.content || "", id);
                        }
                    }
                    // Track last message for DM/room preview
                    if (msg.local && (msg.source === "local_dm" || msg.source === "relay")) {
                        dmLastMessages[id] = {
                            snippet: (msg.content || "").slice(0, 40),
                            timestamp: msg.timestamp || new Date().toISOString(),
                        };
                        if (hiddenDms.has(id)) { hiddenDms.delete(id); localStorage.setItem("proxion_hidden_dms", JSON.stringify([...hiddenDms])); }
                        renderDmSidebar();
                    }
                    if (msg.local && msg.source === "room") {
                        roomLastMessages[id] = {
                            snippet: (msg.content || "").slice(0, 40),
                            senderName: msg.from_display_name || (msg.from_webid || "").slice(0, 8),
                            timestamp: msg.timestamp || new Date().toISOString(),
                        };
                        updateRoomPreview(id);
                    }
                    break;
                }
                case "relay_pending": {
                    const el = document.querySelector(`.message[data-message-id="${event.message_id}"]`);
                    if (el && !el.querySelector(".relay-pending-badge")) {
                        const badge = document.createElement("span");
                        badge.className = "relay-pending-badge";
                        badge.title = "Queued — peer gateway unreachable, will retry";
                        badge.textContent = "⏳";
                        el.appendChild(badge);
                    }
                    break;
                }
                case "relay_delivered": {
                    // R9.4.2: Update pending badge to delivered ✓
                    const msgEl = document.querySelector(`[data-message-id="${event.message_id}"]`);
                    if (msgEl) {
                        const badge = msgEl.querySelector('.relay-pending-badge');
                        if (badge) { badge.textContent = '✓'; badge.classList.replace('relay-pending', 'relay-delivered'); }
                    }
                    break;
                }
                case "link_preview":
                    renderLinkPreview(event);
                    break;
                case "identity":
                    // pub_hex is the gateway's signing key (for pod crypto) — not the user's DID
                    selfPubHex = event.pub_hex || null;
                    turnUrl = event.turn_url;
                    turnSecret = event.turn_secret;
                    // selfWebId stays as clientDid (set at startup); no re-registration needed
                    break;
                case "auth_challenge":
                    _respondToAuthChallenge(event.nonce).catch(() => {});
                    break;
                case "auth_failed":
                    console.warn('[Proxion] Auth failed:', event.reason);
                    break;
                case "registered":
                    if (event.turn) _turnIceServer = event.turn;
                    // Auth is complete — now safe to send all init commands that
                    // require an authenticated socket (they were withheld from onopen).
                    (function _postAuthInit() {
                        if (!socket || socket.readyState !== WebSocket.OPEN) return;
                        const _podWid = localStorage.getItem("proxion_pod_webid");
                        if (_podWid) socket.send(JSON.stringify({cmd: "link_pod", webid: _podWid}));
                        const _statusMsg = localStorage.getItem("proxion_status_message");
                        if (_statusMsg) socket.send(JSON.stringify({cmd: "set_presence", status: "online", status_message: _statusMsg}));
                        const _rcpts = localStorage.getItem("proxion_receipts_enabled") !== "0";
                        socket.send(JSON.stringify({cmd: "set_receipts_enabled", enabled: _rcpts}));
                        const _prvw = localStorage.getItem("proxion_link_previews_enabled") === "1";
                        socket.send(JSON.stringify({cmd: "set_link_previews_enabled", enabled: _prvw}));
                        socket.send(JSON.stringify({cmd: "pod_status"}));
                        // Defer discovery commands 150ms so a flapping connection dies first
                        setTimeout(() => {
                            if (!socket || socket.readyState !== WebSocket.OPEN) return;
                            document.getElementById("room-list").innerHTML = "";
                            document.getElementById("dm-list").innerHTML = "";
                            socket.send(JSON.stringify({cmd: "get_rooms"}));
                            socket.send(JSON.stringify({cmd: "get_dms"}));
                            socket.send(JSON.stringify({cmd: "get_identity"}));
                            socket.send(JSON.stringify({cmd: "list_friend_requests"}));
                            socket.send(JSON.stringify({cmd: "get_relationships"}));
                            requestNotifPermission();
                        }, 150);
                    })();
                    break;
                case "config":
                    // Sync pod state from authoritative gateway truth first
                    if (event.pod_connected) {
                        localStorage.setItem("proxion_pod_connected", "1");
                        if (event.pod_webid) localStorage.setItem("proxion_pod_webid", event.pod_webid);
                        if (event.pod_url)   localStorage.setItem("proxion_css_url", event.pod_url);
                        setPodBanner(false);
                        // Show the sign-out button in settings (same as pod_status does)
                        {
                            const _cd = document.getElementById("settings-pod-connected");
                            const _dd = document.getElementById("settings-pod-disconnected");
                            const _we = document.getElementById("settings-pod-webid");
                            if (_cd) _cd.style.display = "block";
                            if (_dd) _dd.style.display = "none";
                            if (_we && event.pod_webid) _we.textContent = event.pod_webid;
                            _updateSettingsPodDot('connected');
                        }
                    } else {
                        // Gateway has no CSS credentials — that's fine if the browser has
                        // a live Solid session (pod ops run via solidSession.fetch directly).
                        if (solidSession.info.isLoggedIn) break;
                        localStorage.removeItem("proxion_pod_connected");
                        const hasName = localStorage.getItem("proxion_display_name");
                        const skipped = localStorage.getItem("proxion_pod_setup_skipped");
                        const bannerDismissed = localStorage.getItem("proxion_pod_banner_dismissed");
                        if (hasName && !skipped) {
                            showOnboarding();
                            obGoto(4, { returning: true });
                        } else if (!hasName) {
                            showOnboarding();
                        } else if (!bannerDismissed) {
                            setPodBanner(true);
                        }
                    }
                    break;
                case "message_edited":
                    handleMessageEdited(event);
                    break;
                case "message_pinned":
                    showToast("Message pinned");
                    if (document.getElementById("pin-panel").style.display !== "none") {
                        showPinPanel();
                    }
                    break;
                case "unpinned":
                    showToast("Message unpinned");
                    if (document.getElementById("pin-panel").style.display !== "none") {
                        showPinPanel();
                    }
                    break;
                case "pins":
                    renderPins(event.pins);
                    break;
                case "reaction_added":
                    handleReactionEvent(event, "add");
                    break;
                case "reaction_removed":
                    handleReactionEvent(event, "remove");
                    break;
                case "read_receipt":
                    // R10.2.2: upgrade delivery badge to ✓✓
                    (function() {
                        const msgEl = document.querySelector(`[data-message-id="${event.message_id}"]`);
                        if (msgEl) {
                            const badge = msgEl.querySelector('.relay-pending-badge, .relay-delivered');
                            if (badge) { badge.textContent = '✓✓'; badge.className = 'relay-read'; }
                        }
                    })();
                    break;
                case "presence":
                    updatePresence(event);
                    break;
                case "presence_update":
                    handlePresenceUpdate(event);
                    break;
                case "all_presence": {
                    const map = event.presence || {};
                    Object.entries(map).forEach(([webid, data]) => {
                        userPresence[webid] = { status: data.status || "offline", updated_at: data.updated_at };
                    });
                    break;
                }
                case "presence_set":
                    if (selfWebId) userPresence[selfWebId] = { status: event.status, updated_at: new Date().toISOString() };
                    break;
                case "rooms":
                    // Re-populate CSS rooms normally; also restore local rooms
                    populateSidebar("room-list", event.rooms.filter(r => !r.local), "room");
                    event.rooms.filter(r => r.local).forEach(r => {
                        addRoomToSidebar(r.id, r.name, r.invite_url);
                        if (r.creator_webid && r.creator_webid === clientDid) {
                            roomCreatorOf.add(r.id);
                        }
                        // Request catch-up history (messages since last seen)
                        if (socket && socket.readyState === WebSocket.OPEN) {
                            const since = localStorage.getItem("proxion_seen_" + r.id);
                            loadRoomHistory(r.id, 100);
                        }
                    });
                    break;
                case "room_created":
                    roomCreatorOf.add(event.room_id);
                    _local_rooms[event.room_id] = { memberWebIds: new Set(selfWebId ? [selfWebId] : []) };
                    addRoomToSidebar(event.room_id, event.name, event.invite_url);
                    podWriteRoomMeta(event.room_id, {
                        room_id: event.room_id,
                        name: event.name,
                        code: event.code,
                        creator_webid: selfWebId,
                        created_at: new Date().toISOString(),
                    }).catch(() => {});
                    _podUpdateRoomIndex(event.room_id, true).catch(() => {});
                    if (solidSession.info.isLoggedIn && selfWebId) {
                        podSetContainerAcl(`rooms/${event.room_id}/`, selfWebId, []).catch(() => {});
                        podWriteRoomMembers(event.room_id, [{ webid: selfWebId, role: 'admin' }])
                            .catch(() => {});
                    }
                    if (window._obFromOnboarding) {
                        window._obFromOnboarding = false;
                        document.getElementById("room-create-modal").style.display = "none";
                        document.getElementById("onboarding-modal").style.display = "flex";
                        const obUrl = document.getElementById("ob-room-invite-url");
                        if (obUrl) {
                            obUrl.textContent = event.invite_url || event.code || "";
                            const display = document.getElementById("ob-invite-display");
                            if (display && obUrl.textContent) display.style.display = "block";
                        }
                        obGoto(6);
                    } else {
                        document.getElementById("room-create-form").style.display = "none";
                        document.getElementById("room-invite-url").textContent = event.invite_url;
                        document.getElementById("room-invite-result").style.display = "";
                        setTimeout(() => {
                            const li = document.getElementById(`nav-${event.room_id}`);
                            if (li) li.click();
                        }, 50);
                    }
                    break;
                case "room_joined":
                    document.getElementById("room-create-modal").style.display = "none";
                    addRoomToSidebar(event.room_id, event.name, event.invite_url);
                    _podUpdateRoomIndex(event.room_id, true).catch(() => {});
                    setTimeout(() => {
                        const li = document.getElementById(`nav-${event.room_id}`);
                        if (li) li.click();
                    }, 50);
                    // R31: register home gateway for federated relay fanout
                    {
                        const _homeGw = localStorage.getItem("proxion_gateway_http_url") || "";
                        if (_homeGw && socket?.readyState === WebSocket.OPEN) {
                            socket.send(JSON.stringify({
                                cmd: "announce_room_join",
                                room_id: event.room_id,
                                code: event.code || "",
                                home_gateway: _homeGw,
                            }));
                        }
                    }
                    break;
                case "room_history":
                    if (activeView && activeView.id === event.room_id) {
                        const _existingIds = new Set(allMessages.map(m => m.message_id));
                        const _newMsgs = (event.messages || []).filter(m => !_existingIds.has(m.message_id));
                        if (_newMsgs.length > 0) {
                            allMessages = [..._newMsgs, ...allMessages];
                            _newMsgs.forEach(m => { messageMap[m.message_id] = m; });
                            renderMessages();
                        }
                    }
                    break;
                case "room_member_joined":
                    if (_local_rooms[event.room_id]) {
                        _local_rooms[event.room_id].memberWebIds = _local_rooms[event.room_id].memberWebIds || new Set(selfWebId ? [selfWebId] : []);
                        if (event.webid) _local_rooms[event.room_id].memberWebIds.add(event.webid);
                        if (solidSession.info.isLoggedIn && selfWebId) {
                            const members = Array.from(_local_rooms[event.room_id].memberWebIds || []);
                            const others = members.filter(w => w !== selfWebId);
                            podSetContainerAcl(`rooms/${event.room_id}/`, selfWebId, others).catch(() => {});
                        }
                    }
                    if (activeView && activeView.id === event.room_id) {
                        const feed = document.getElementById("message-feed");
                        const el = document.createElement("div");
                        el.className = "system-msg";
                        el.textContent = "Someone joined the room.";
                        feed.appendChild(el);
                        feed.scrollTop = feed.scrollHeight;
                    }
                    break;
                case "dms":
                    populateSidebar("dm-list", event.dms, "dm");
                    break;
                case "search_results":
                    renderSearchResults(event);
                    break;
                case "voice_invite":
                    showVoiceBanner(event);
                    showOsNotification('Incoming Call',
                        `${event.display_name || 'Someone'} is calling`);
                    break;
                case "voice_answer":
                    handleVoiceAnswer(event);
                    break;
                case "ice_candidate":
                    handleIceCandidate(event);
                    break;
                case "voice_hangup":
                    handleVoiceHangup(event);
                    break;
                case "voice_signal":
                    handleVoiceSignalRelay(event);
                    break;
                case "voice_peer_joined":
                    handleVoicePeerJoined(event);
                    break;
                case "voice_peer_present":
                    handleVoicePeerPresent(event);
                    break;
                case "voice_peer_left":
                    handleVoicePeerLeft(event);
                    break;
                // Cross-gateway voice channel relay deliveries
                case "voice_channel_peer_joined":
                    handleVoicePeerJoined(event);
                    break;
                case "voice_channel_peer_present":
                    handleVoicePeerPresent(event);
                    break;
                case "peer_discovered":
                    handlePeerDiscovered(event);
                    break;
                case "typing":
                    handleTyping(event);
                    break;
                case "did_resolved": {
                    const peerDid = event.did;
                    const shortLabel = peerDid.slice(0, 16) + "…";
                    const navId = "local-" + peerDid.replace(/[^a-zA-Z0-9]/g, "-");
                    if (!localDmPeers[navId]) {
                        localDmPeers[navId] = { display_name: shortLabel, peer_webid: peerDid };
                        renderDmSidebar();
                    }
                    if (solidSession.info.isLoggedIn && selfWebId) {
                        const dmThreadId = event.thread_id || event.dm_room_id || navId;
                        if (dmThreadId) {
                            podSetContainerAcl(`rooms/${dmThreadId}/`, selfWebId, [event.webid || event.did]).catch(() => {});
                        }
                    }
                    openLocalDmThread(navId, shortLabel, peerDid);
                    break;
                }
                case "local_dms": {
                    // Restore local DM threads persisted from previous sessions
                    event.dms.forEach(dm => {
                        localDmPeers[dm.id] = { display_name: dm.name, peer_webid: dm.peer_webid };
                        // Request catch-up unread count
                        if (socket && socket.readyState === WebSocket.OPEN) {
                            const since = localStorage.getItem("proxion_seen_" + dm.id);
                            loadRoomHistory(dm.id, 100);
                        }
                    });
                    renderDmSidebar();
                    break;
                }
                case "room_members": {
                    currentRoomMembers = event.members || [];
                    renderMembersPanel(event.members);
                    // Also populate room members modal if it's open
                    const list = document.getElementById("room-members-list");
                    if (list && _membersRoomId) {
                        if (!event.members || event.members.length === 0) {
                            list.innerHTML = "<p style='color:#94a3b8'>No members found.</p>";
                            break;
                        }
                        const isOwner = roomCreatorOf.has(event.room_id);
                        list.innerHTML = event.members.map(m => {
                            const isSelf = m.webid === clientDid;
                            const kickBtn = isOwner && !isSelf
                                ? `<button data-rm-action="kick" data-room-id="${event.room_id}" data-webid="${m.webid}"
                                          style="margin-left:4px;background:#7f1d1d;border:none;color:#fca5a5;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:0.75em;flex-shrink:0;"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z"/></svg></button>` : "";
                            const banBtn = isOwner && !isSelf
                                ? `<button data-rm-action="ban" data-room-id="${event.room_id}" data-webid="${m.webid}" style="margin-left:4px;background:#451a03;border:none;color:#fed7aa;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:0.75em;">Ban</button>` : "";
                            const muteBtn = isOwner && !isSelf
                                ? `<button data-rm-action="mute" data-room-id="${event.room_id}" data-webid="${m.webid}" style="margin-left:4px;background:#1c1917;border:none;color:#a8a29e;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:0.75em;">Mute</button>` : "";
                            const ownerBtn = isOwner && !isSelf
                                ? `<button data-rm-action="transfer" data-room-id="${event.room_id}" data-webid="${m.webid}"
                                          style="margin-left:4px;background:#1e3a5f;border:none;color:#7dd3fc;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:0.75em;flex-shrink:0;"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z"/></svg></button>` : "";
                            const displayName = m.display_name || m.webid.slice(0, 20);
                            const label = isSelf ? `${displayName} (you)` : displayName;
                            return `<div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid #334155;">
                                <span style="font-size:0.8em;color:#cbd5e1;word-break:break-all;flex:1;">${label}</span>
                                ${ownerBtn}${kickBtn}${banBtn}${muteBtn}
                            </div>`;
                        }).join("");
                    }
                    break;
                }
                case "member_kicked":
                    // Refresh members list if modal is open for this room
                    if (event.room_id === _membersRoomId) {
                        showRoomMembers(event.room_id);
                    }
                    break;
                case "kicked_from_room": {
                    // Remove room from sidebar and clear view if active
                    const navEl = document.getElementById(`nav-${event.room_id}`);
                    if (navEl) navEl.remove();
                    if (activeView && activeView.id === event.room_id) {
                        activeView = null;
                        showEmptyState();
                        document.getElementById("message-feed").innerHTML = '<div class="system-msg">You were removed from this room.</div>';
                    }
                    break;
                }
                case "left_room": {
                    const leftId = event.room_id;
                    roomCreatorOf.delete(leftId);
                    const leftLi = document.getElementById(`nav-${leftId}`);
                    if (leftLi) leftLi.remove();
                    if (activeView && activeView.id === leftId) {
                        activeView = null;
                        showEmptyState();
                        const msg = event.deleted
                            ? "Room deleted — you were the last member."
                            : event.transferred_to
                            ? `You left the room. Ownership transferred to ${escHtml(event.transferred_to)}.`
                            : "You left the room.";
                        document.getElementById("message-feed").innerHTML = `<div class="system-msg">${msg}</div>`;
                        document.getElementById("members-toggle").style.display = "none";
                        document.getElementById("leave-room-btn").style.display = "none";
                        document.getElementById("delete-room-btn").style.display = "none";
                        document.getElementById("members-panel").style.display = "none";
                        document.getElementById("members-panel").classList.remove("mobile-open");
                    }
                    break;
                }
                case "room_deleted": {
                    roomCreatorOf.delete(event.room_id);
                    const delLi = document.getElementById(`nav-${event.room_id}`);
                    if (delLi) delLi.remove();
                    if (activeView && activeView.id === event.room_id) {
                        activeView = null;
                        showEmptyState();
                        document.getElementById("message-feed").innerHTML = `<div class="system-msg">The room "${escHtml(event.room_name)}" was deleted by its owner.</div>`;
                        document.getElementById("members-toggle").style.display = "none";
                        document.getElementById("leave-room-btn").style.display = "none";
                        document.getElementById("delete-room-btn").style.display = "none";
                        document.getElementById("members-panel").style.display = "none";
                    }
                    break;
                }
                case "ownership_transferred": {
                    if (event.new_owner_did === clientDid) {
                        roomCreatorOf.add(event.room_id);
                        if (activeView && activeView.id === event.room_id) {
                            document.getElementById("delete-room-btn").style.display = "inline-block";
                        }
                        showToast(`You are now the owner of this room.`);
                    } else {
                        roomCreatorOf.delete(event.room_id);
                        if (activeView && activeView.id === event.room_id) {
                            document.getElementById("delete-room-btn").style.display = "none";
                        }
                        showToast(`Room ownership transferred to ${event.new_owner_name}.`);
                    }
                    break;
                }
                case "ownership_transfer_offer": {
                    showConfirm(
                        `${event.from_name} wants to transfer ownership of "${event.room_name}" to you. Accept?`,
                        () => socket.send(JSON.stringify({cmd: "accept_ownership", room_id: event.room_id})),
                        () => socket.send(JSON.stringify({cmd: "decline_ownership", room_id: event.room_id}))
                    );
                    break;
                }
                case "history": {
                    const msgs = event.messages || [];
                    const tid = event.thread_id;
                    const isActive = activeView && activeView.id === tid;
                    const feed = document.getElementById("message-feed");
                    const isPagination = msgs.length > 0 && isActive && allMessages.length > 0
                        && msgs[msgs.length - 1].timestamp < allMessages[0].timestamp;

                    if (isPagination) {
                        const oldHeight = feed.scrollHeight;
                        msgs.forEach(m => {
                            if (!allMessages.find(x => x.message_id === m.message_id)) {
                                allMessages.unshift(m);
                                messageMap[m.message_id] = m;
                            }
                        });
                        const slice = allMessages.slice(0, RENDER_WINDOW);
                        feed.innerHTML = "";
                        _lastRenderedDate = null;
                        _renderThreaded(slice, feed);
                        feed.scrollTop = feed.scrollHeight - oldHeight;
                        _loadingOlderHistory = false;
                    } else if (isActive) {
                        if (msgs.length > 0) {
                            msgs.forEach(m => renderMessage(m));
                        } else {
                            maybeShowEmptyState();
                        }
                    } else if (msgs.length > 0) {
                        unreadCounts[tid] = (unreadCounts[tid] || 0) + msgs.length;
                        updateSidebarBadge(tid);
                    }
                    break;
                }
                case "local_history": {
                    const msgs = event.messages || [];
                    const tid = event.thread_id;
                    const lastReadTs = event.last_read_ts || 0;
                    const feed = document.getElementById("message-feed");
                    // Remove loading skeleton
                    const skel = document.getElementById("history-skeleton");
                    if (skel) skel.remove();
                    const isActive = activeView && activeView.id === tid;
                    const isPagination = msgs.length > 0 && isActive && allMessages.length > 0
                        && msgs[msgs.length - 1].timestamp < allMessages[0].timestamp;

                    if (isPagination) {
                        // Prepend older messages at the top
                        const oldHeight = feed.scrollHeight;
                        _lastRenderedDate = null; // rebuild date dividers from scratch
                        msgs.forEach(m => {
                            if (!allMessages.find(x => x.message_id === m.message_id)) {
                                allMessages.unshift(m);
                                messageMap[m.message_id] = m;
                            }
                        });
                        // Re-render keeping scroll position
                        const slice = allMessages.slice(0, RENDER_WINDOW);
                        feed.innerHTML = "";
                        _lastRenderedDate = null;
                        _renderThreaded(slice, feed);
                        feed.scrollTop = feed.scrollHeight - oldHeight;
                        _loadingOlderHistory = false;
                    } else if (isActive) {
                        // Initial load or catch-up
                        if (msgs.length > 0) {
                            // Unread divider: if this is catch-up (we had messages before), mark new ones
                            const hadMessages = allMessages.length > 0;
                            if (hadMessages) {
                                const divider = document.createElement("div");
                                divider.className = "unread-divider";
                                divider.innerHTML = "<span>New Messages</span>";
                                feed.appendChild(divider);
                            }
                            msgs.forEach(m => renderMessage(m));
                        } else {
                            maybeShowEmptyState();
                        }
                    } else {
                        // Not viewing this thread: count unseen messages as unread (skip if muted)
                        if (msgs.length > 0 && !mutedThreads.has(tid)) {
                            const lastReadTime = new Date(lastReadTs * 1000);
                            const unreadCount = msgs.filter(m => new Date(m.timestamp) > lastReadTime).length;
                            unreadCounts[tid] = (unreadCounts[tid] || 0) + unreadCount;
                            updateSidebarBadge(tid);
                        }
                    }
                    // Apply persisted reactions
                    if (event.reactions && event.reactions.length > 0) {
                        event.reactions.forEach(r => {
                            if (!messageReactions[r.message_id]) messageReactions[r.message_id] = {};
                            if (!messageReactions[r.message_id][r.emoji]) messageReactions[r.message_id][r.emoji] = [];
                            if (!messageReactions[r.message_id][r.emoji].includes(r.sender_webid)) {
                                messageReactions[r.message_id][r.emoji].push(r.sender_webid);
                            }
                        });
                        const seen = new Set();
                        event.reactions.forEach(r => {
                            if (!seen.has(r.message_id)) { seen.add(r.message_id); renderReactions(r.message_id); }
                        });
                    }
                    break;
                }
                case "message_deleted": {
                    const el = document.getElementById(`msg-${event.message_id}`);
                    if (el) el.remove();
                    allMessages = allMessages.filter(m => m.message_id !== event.message_id);
                    delete messageMap[event.message_id];
                    if (event.message_id && event.thread_id) {
                        const _isRoom = !!(activeView && activeView.type === 'local_room');
                        podDeleteMessage(event.thread_id, event.message_id, _isRoom).catch(() => {});
                    }
                    break;
                }
                case "my_address":
                    if (event.proxion_address) {
                        window.proxionAddress = event.proxion_address;
                        localStorage.setItem("proxion_my_address", event.proxion_address);
                        const addrEl = document.getElementById("settings-proxion-address");
                        if (addrEl) addrEl.textContent = event.proxion_address;
                        updateMyAddressBar(event.proxion_address);
                        // Populate ob-my-addr if onboarding step-6 is visible
                        const obAddr = document.getElementById("ob-my-addr");
                        if (obAddr) obAddr.textContent = event.proxion_address;
                    }
                    // R28: NAT warning banner (shown once per session)
                    if (!sessionStorage.getItem('proxion_nat_warned')) {
                        fetch('/.well-known/proxion').then(r => r.json()).then(d => {
                            if (d.nat_warning) {
                                sessionStorage.setItem('proxion_nat_warned', '1');
                                _showNatWarning();
                            }
                        }).catch(() => {});
                    }
                    // R8.2.2: Store invite_link from my_address event
                    if (event.invite_link) {
                        window.proxionInviteLink = event.invite_link;
                    }
                    // R17: store short invite URL for QR code
                    if (event.short_invite_url) {
                        window.proxionShortInviteUrl = event.short_invite_url;
                    }
                    // R16: store gateway HTTP URL so wizard can POST to /setup/pod
                    if (event.gateway_http_url) {
                        localStorage.setItem("proxion_gateway_http_url", event.gateway_http_url);
                    }
                    break;
                case "css_connected":
                    localStorage.setItem("proxion_my_address", event.proxion_address || "");
                    localStorage.setItem("proxion_pod_connected", "1");
                    localStorage.removeItem("proxion_pod_setup_skipped");
                    if (event.webid) {
                        socket.send(JSON.stringify({cmd: "link_pod", webid: event.webid}));
                        onPodLoggedIn(event.webid);
                    }
                    if (event.proxion_address) updateMyAddressBar(event.proxion_address);
                    {
                        const addrEl = document.getElementById("settings-proxion-address");
                        if (addrEl && event.proxion_address) addrEl.textContent = event.proxion_address;
                    }
                    break;
                case "css_error":
                    {
                        localStorage.removeItem("proxion_pod_connected");
                        setPodBanner(true);
                        const connDiv = document.getElementById("settings-pod-connected");
                        const discDiv = document.getElementById("settings-pod-disconnected");
                        if (connDiv) connDiv.style.display = "none";
                        if (discDiv) discDiv.style.display = "block";
                        if (event.message) showToast("Pod connection failed: " + event.message, "error");
                        // If the browser has a live Solid session but the gateway lost its
                        // credentials (e.g. after sign-out cleared pod_creds.json), drop the
                        // user onto the pod-credentials step so they can reconnect.
                        // Only prompt for credentials on genuine auth failures,
                        // not transient WebSocket drops (1001 going away).
                        const isCredError = event.message && !event.message.includes('1001') && !event.message.includes('going away');
                        if (solidSession.info.isLoggedIn && isCredError) {
                            showOnboarding();
                            obGoto(4, { returning: true });
                        }
                    }
                    break;
                case "friend_request_sent": {
                    _pendingFriendRequest = false;
                    const _modal = document.getElementById("add-peer-modal");
                    if (_modal) _modal.style.display = "none";
                    const _sb = document.getElementById("add-peer-submit-btn");
                    if (_sb) { _sb.disabled = false; _sb.textContent = "Send Request"; }
                    const _addr = event.target_address || event.target_did || "";
                    const _short = _addr.includes("@") ? _addr.split("@").pop() : _addr.slice(0, 16) + "…";
                    showToast("Request sent to " + _short);
                    break;
                }
                case "friend_request_received":
                    renderPendingInvite(event);
                    // Persist to pod so it survives refresh
                    if (event.invitation_id) {
                        podWriteInvite(event.invitation_id, event).catch(() => {});
                    }
                    break;
                case "friend_request_accepted": {
                    const el = document.getElementById("fri-" + event.invitation_id);
                    if (el) el.remove();
                    refreshFriendRequestsBadge();
                    // Write cert to pod and remove the pending invite
                    if (event.certificate) {
                        const cert = event.certificate;
                        const certId = cert.certificate_id;
                        if (certId) podWriteContact(certId, cert).catch(() => {});
                        if (event.invitation_id) podDeleteInvite(event.invitation_id).catch(() => {});
                        showToast("Contact added!");
                        if (socket?.readyState === WebSocket.OPEN)
                            socket.send(JSON.stringify({ cmd: 'get_relationships' }));
                        // Store cert for auto-open after relationships reload
                        if (cert.certificate_id && cert.subject) {
                            sessionStorage.setItem("proxion_open_cert_after_load", cert.certificate_id);
                        }
                    } else {
                        showToast("Friend request accepted — waiting for certificate…");
                    }
                    break;
                }
                case "contact_added": {
                    // Requester side receives this after acceptor's gateway POSTs back
                    const cert = event.certificate;
                    if (cert?.certificate_id) {
                        podWriteContact(cert.certificate_id, cert).catch(() => {});
                        if (event.invitation_id) podDeleteInvite(event.invitation_id).catch(() => {});
                    }
                    if (event.peer_did && event.x25519_pub) cachePeerPub(event.peer_did, event.x25519_pub);
                    showToast("Contact connected: " + (event.peer_did || "peer").slice(8, 22) + "…");
                    if (socket?.readyState === WebSocket.OPEN)
                        socket.send(JSON.stringify({ cmd: 'get_relationships' }));
                    break;
                }
                case "contact_revoked":
                    // R12.4.1+R12.4.2: Mark contact as revoked in sidebar
                    (function() {
                        const certId = event.cert_id;
                        const contactEl = document.querySelector(`[data-cert-id="${certId}"]`);
                        if (contactEl) {
                            const badge = document.createElement("span");
                            badge.textContent = " ⛔";
                            badge.title = "Contact revoked";
                            contactEl.appendChild(badge);
                        }
                        // Disable message input if this contact is currently open
                        if (window.currentThread === certId || window.currentCertId === certId) {
                            const inp = document.getElementById("message-input") || document.getElementById("chat-input");
                            if (inp) { inp.disabled = true; inp.placeholder = "This contact has been revoked."; }
                        }
                    })();
                    showToast("A contact has been revoked.");
                    break;
                case "relationship_established":
                    document.querySelectorAll("[data-peer-did='" + (event.peer_did || "") + "']")
                        .forEach(el => el.remove());
                    refreshFriendRequestsBadge();
                    showToast("Connected with " + (event.peer_did || "peer").slice(8, 22) + "…");
                    if (socket && socket.readyState === WebSocket.OPEN)
                        socket.send(JSON.stringify({cmd: "get_relationships"}));
                    break;
                case "relationships":
                    renderContacts(event.contacts);
                    const _openAfter = sessionStorage.getItem("proxion_open_cert_after_load");
                    if (_openAfter) {
                        sessionStorage.removeItem("proxion_open_cert_after_load");
                        const _newContact = (event.contacts || []).find(c => c.certificate_id === _openAfter);
                        if (_newContact) openContactThread(_newContact);
                    }
                    (event.contacts || []).forEach(c => {
                        if (c.peer_did && c.x25519_pub) cachePeerPub(c.peer_did, c.x25519_pub);
                        if (c.unread_count > 0) {
                            unreadCounts[c.certificate_id] = (unreadCounts[c.certificate_id] || 0) + c.unread_count;
                            updateSidebarBadge(c.certificate_id);
                        }
                    });
                    break;
                case "friend_requests":
                    (event.pending || []).forEach(renderPendingInvite);
                    break;
                case "cert_expiring_soon": {
                    const warnKey = "cert_warned_" + event.certificate_id;
                    if (sessionStorage.getItem(warnKey)) break;
                    sessionStorage.setItem(warnKey, "1");
                    const days = Math.ceil((event.expires_at - Date.now() / 1000) / 86400);
                    showToast("Your connection with " + (event.peer_did || "a contact").slice(8, 18) + "… expires in " + days + " day" + (days === 1 ? "" : "s") + ".");
                    break;
                }
                case "pod_status": {
                    // event.connected — user-initiated connect/disconnect
                    // event.available — watchdog mid-session reachability change
                    const isConnected = event.connected;
                    const isAvailable = event.available;

                    if (isConnected === true) {
                        localStorage.setItem("proxion_pod_connected", "1");
                        if (event.webid)   localStorage.setItem("proxion_pod_webid", event.webid);
                        if (event.pod_url) localStorage.setItem("proxion_css_url", event.pod_url);
                        setPodBanner(false);
                    } else if (isConnected === false && !solidSession.info.isLoggedIn) {
                        localStorage.removeItem("proxion_pod_connected");
                        if (localStorage.getItem("proxion_display_name")) setPodBanner(true);
                    }

                    // Watchdog reachability events (available field, no connected field)
                    if (isConnected === undefined && isAvailable === false) {
                        showToast("Pod unreachable — messages may be delayed", "warning");
                    } else if (isConnected === undefined && isAvailable === true) {
                        showToast("Pod reconnected");
                    }

                    const connDiv = document.getElementById("settings-pod-connected");
                    const discDiv = document.getElementById("settings-pod-disconnected");
                    const webidEl = document.getElementById("settings-pod-webid");
                    if (isConnected === true) {
                        if (connDiv) connDiv.style.display = "block";
                        if (discDiv) discDiv.style.display = "none";
                        if (webidEl && event.webid) webidEl.textContent = event.webid;
                    } else if (isConnected === false && !solidSession.info.isLoggedIn) {
                        // Only show disconnected if the browser also has no Solid session.
                        // Gateway lacking CSS credentials ≠ user logged out.
                        if (connDiv) connDiv.style.display = "none";
                        if (discDiv) discDiv.style.display = "block";
                    }

                    // R16.4.2: keep settings header dot in sync
                    if (isConnected === true) {
                        _updateSettingsPodDot('connected');
                    } else if (isConnected === false) {
                        _updateSettingsPodDot('none');
                    } else if (isAvailable === false) {
                        _updateSettingsPodDot('unreachable');
                    } else if (isAvailable === true) {
                        _updateSettingsPodDot('connected');
                    }
                    break;
                }
                case "error": {
                    if (_pendingFriendRequest && (event.message in _friendRequestErrors || event.message === "delivery_failed" || event.message === "invalid_address")) {
                        _pendingFriendRequest = false;
                        const _sb2 = document.getElementById("add-peer-submit-btn");
                        if (_sb2) { _sb2.disabled = false; _sb2.textContent = "Send Request"; }
                        const _errEl = document.getElementById("add-peer-error");
                        if (_errEl) _errEl.textContent = _friendRequestErrors[event.message] || (event.detail || event.message);
                        break;
                    }
                    showToast("Gateway Error: " + event.message, "error");
                    break;
                }
                case "message_fetched": {
                    const m = event.message;
                    if (!m) break;
                    messageMap[m.message_id] = m;
                    // Fill in any placeholders waiting for this message
                    document.querySelectorAll(`.reply-context-loading[data-reply-target="${m.message_id}"]`)
                        .forEach(el => {
                            const parentName = m.from_display_name || (m.from_webid || "").slice(0, 12);
                            const snippet = (m.content || "").slice(0, 80);
                            el.classList.remove("reply-context-loading");
                            el.innerHTML = `<span class="reply-connector"></span><b style="color:${webidColor(m.from_webid)};margin-right:2px;">${escHtml(parentName)}</b><span>${escHtml(snippet)}</span>`;
                        });
                    break;
                }
                case "session_list": {
                    const sl = document.getElementById("sessions-list");
                    if (!sl) break;
                    sl.innerHTML = event.sessions.map(s => `
                        <div class="session-item">
                            <div class="session-info">
                                <span class="session-ip">${escHtml(s.ip_addr || "unknown")}</span>
                                <span class="session-time">${formatTimestamp(s.connected_at)}</span>
                                ${s.is_current ? '<span class="session-current">● this device</span>' : ''}
                            </div>
                            ${!s.is_current ? `<button class="session-revoke-btn" data-session-id="${s.session_id}">Revoke</button>` : ''}
                        </div>`).join('') || '<div style="color:#94a3b8">No sessions found.</div>';
                    sl.querySelectorAll('.session-revoke-btn').forEach(btn => {
                        btn.addEventListener('click', () => {
                            if (socket) socket.send(JSON.stringify({cmd:'revoke_session',session_id:btn.dataset.sessionId}));
                        });
                    });
                    break;
                }
                case "session_revoked":
                    showToast("This session was revoked from another device.", "error");
                    setTimeout(() => { if (socket) socket.close(); }, 1500);
                    break;
                case "logout_all_complete":
                    showToast(`Logged out ${event.revoked_count || 0} other session(s).`);
                    break;
                case "pod_auth_error": {
                    showToast((event.message || "Pod credentials expired") + " — re-enter in Settings", "warning");
                    break;
                }
                case "pod_auth_restored": {
                    showToast("Pod credentials refreshed successfully.", "success");
                    break;
                }
                case "dm_messages_expired": {
                    const expBefore = event.before_timestamp;
                    if (activeView && (activeView.certId === event.thread_id || activeView.id === event.thread_id) && expBefore) {
                        document.querySelectorAll('#message-feed .message').forEach(el => {
                            const msgId = el.dataset.messageId;
                            const msg = msgId && messageMap[msgId];
                            if (msg && msg.timestamp && msg.timestamp < expBefore) {
                                el.remove();
                                allMessages = allMessages.filter(m => m.message_id !== msgId);
                                delete messageMap[msgId];
                            }
                        });
                    }
                    break;
                }
                case "webhook_created":
                    if (event.direction === "incoming") {
                        document.getElementById("webhook-url-display").textContent = event.webhook_url || "";
                        const wm = document.getElementById("webhook-created-modal");
                        if (wm) { wm.style.display = "flex"; }
                    } else {
                        showToast("Outgoing webhook created. Copy the secret now — it won't be shown again.");
                        if (event.secret) { showToast("Secret: " + event.secret, "info"); }
                    }
                    podWriteWebhook(event.id, {
                        direction: event.direction, bot_name: event.bot_name || "Bot",
                        thread_id: activeView ? activeView.id : "", url: event.url || "",
                        token: event.token || event.secret || "",
                    }).catch(() => {});
                    break;
                case "webhook_deleted":
                    showToast("Webhook deleted.");
                    if (event.id) podDeleteWebhook(event.id).catch(() => {});
                    break;
                case "webhook_list": {
                    const wl = document.getElementById("webhook-list-area");
                    if (!wl) break;
                    if (!event.webhooks || !event.webhooks.length) { wl.innerHTML = '<em style="color:#94a3b8">No webhooks.</em>'; break; }
                    wl.innerHTML = event.webhooks.map(h => `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.85em;">
                        <span style="flex:1">${escHtml(h.bot_name)} (${escHtml(h.direction)})</span>
                        <button data-del-wh="${escHtml(h.id)}" style="background:#7f1d1d;border:none;color:#fca5a5;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:0.8em;">Delete</button>
                    </div>`).join('');
                    wl.querySelectorAll('[data-del-wh]').forEach(btn => {
                        btn.addEventListener('click', () => socket && socket.send(JSON.stringify({cmd:'delete_webhook',id:btn.dataset.delWh})));
                    });
                    break;
                }
                case "disappear_timer_updated": {
                    const ms = event.ms || 0;
                    if (activeView && activeView.id === event.room_id) {
                        updateDisappearBanner(ms);
                        const sel = document.getElementById("disappear-timer-select");
                        if (sel) sel.value = String(ms);
                    }
                    break;
                }
                case "disappear_timer": {
                    const ms2 = event.ms || 0;
                    updateDisappearBanner(ms2);
                    const sel2 = document.getElementById("disappear-timer-select");
                    if (sel2) sel2.value = String(ms2);
                    break;
                }
                case "member_role_updated": {
                    if (event.room_id && event.webid) {
                        showToast(`Role updated: ${escHtml(event.webid.slice(0,20))} → ${event.role}`);
                        if (activeView && activeView.id === event.room_id) {
                            socket && socket.send(JSON.stringify({cmd:"get_room_members", room_id: event.room_id}));
                        }
                    }
                    break;
                }
                case "room_roles":
                    break;
                case "scheduled_list": {
                    const sp = document.getElementById("scheduled-msgs-list");
                    if (!sp) break;
                    if (!event.scheduled || !event.scheduled.length) { sp.innerHTML = '<em style="color:#94a3b8">None scheduled.</em>'; break; }
                    sp.innerHTML = event.scheduled.map(s => `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.85em;">
                        <span style="flex:1">${escHtml((s.content||'').slice(0,40))} <em style="color:#94a3b8">(${new Date(s.send_at*1000).toLocaleString()})</em></span>
                        <button data-cancel-sched="${s.id}" style="background:#334155;border:none;color:#f1f5f9;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:0.8em;">Cancel</button>
                    </div>`).join('');
                    sp.querySelectorAll('[data-cancel-sched]').forEach(btn => {
                        btn.addEventListener('click', () => socket && socket.send(JSON.stringify({cmd:'cancel_scheduled',id:btn.dataset.cancelSched})));
                    });
                    break;
                }
                case "message_scheduled":
                case "scheduled_created":
                    showToast("Message scheduled.");
                    if (event.id && event.thread_id) {
                        podWriteScheduled(event.id, event.thread_id, event.send_at || '', event.content_preview || '').catch(() => {});
                    }
                    break;
                case "scheduled_cancelled":
                    showToast("Scheduled message cancelled.");
                    if (event.id) podDeleteScheduled(event.id).catch(() => {});
                    break;
                case "devices": {
                    const container = document.getElementById("settings-devices-list");
                    if (!container) break;
                    const devs = event.devices || [];
                    if (devs.length === 0) {
                        container.innerHTML = '<span style="color:#475569;">No linked devices.</span>';
                        break;
                    }
                    container.innerHTML = devs.map(d => {
                        const label = escHtml(d.display_name || d.device_id.slice(0, 16));
                        const since = d.registered_at
                            ? new Date(d.registered_at * 1000).toLocaleDateString() : "";
                        return `<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;border-bottom:1px solid #1e293b;gap:8px;">
                            <span style="flex:1">${label}<br><span style="color:#475569;font-size:0.8em;">${since}</span></span>
                            <button data-device-id="${escHtml(d.device_id)}" style="background:transparent;border:none;color:#f87171;font-size:0.8em;cursor:pointer;padding:2px 6px;">Revoke</button>
                        </div>`;
                    }).join("");
                    container.querySelectorAll("[data-device-id]").forEach(btn => {
                        btn.addEventListener("click", () => {
                            const id = btn.dataset.deviceId;
                            showConfirm(`Revoke device "${id.slice(0, 16)}"?`, () => {
                                socket.send(JSON.stringify({cmd: "unregister_device", device_id: id}));
                                btn.closest("div").remove();
                            });
                        });
                    });
                    break;
                }
                case "member_banned":
                    _appendSystemMsg(`${event.display_name || event.webid.slice(-12)} was banned${event.reason ? ' (' + escHtml(event.reason) + ')' : ''}`);
                    if (_membersRoomId === event.room_id) showRoomMembers(event.room_id);
                    break;
                case "member_unbanned":
                    _appendSystemMsg(`${event.webid.slice(-12)} was unbanned`);
                    break;
                case "member_muted":
                    _appendSystemMsg(`${event.webid.slice(-12)} was muted` +
                        (event.expires_at ? ` until ${new Date(event.expires_at * 1000).toLocaleTimeString()}` : ''));
                    break;
                case "member_unmuted":
                    _appendSystemMsg(`${event.webid.slice(-12)} was unmuted`);
                    break;
                case "room_bans": {
                    const list = document.getElementById("room-bans-list");
                    if (!list) break;
                    const bans = event.bans || [];
                    if (bans.length === 0) {
                        list.innerHTML = '<p style="color:#78716c;font-size:0.85em;">No banned members.</p>';
                        break;
                    }
                    list.innerHTML = bans.map(b => `
                        <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid #292524;">
                          <span style="flex:1;font-size:0.85em;">
                            <span style="color:#f5f5f4;">${escHtml(b.display_name || b.banned_did.slice(-12))}</span>
                            ${b.reason ? `<br><span style="color:#78716c;font-size:0.8em;">${escHtml(b.reason)}</span>` : ''}
                          </span>
                          <button data-unban-did="${escHtml(b.banned_did)}" data-room-id="${escHtml(event.room_id)}"
                                  style="background:transparent;border:none;color:#4ade80;font-size:0.8em;cursor:pointer;">
                            Unban
                          </button>
                        </div>`).join("");
                    list.querySelectorAll("[data-unban-did]").forEach(btn => {
                        btn.addEventListener("click", () => {
                            socket.send(JSON.stringify({cmd: "unban_member", room_id: btn.dataset.roomId, webid: btn.dataset.unbanDid}));
                            btn.closest("div").remove();
                        });
                    });
                    break;
                }
                case "message_readers": {
                    const el = document.querySelector(`.seen-by-row[data-msg-id="${event.message_id}"]`);
                    if (!el) break;
                    const readers = (event.readers || []).filter(r => r.receiver_webid !== selfWebId);
                    if (readers.length === 0) break;
                    const names = readers.slice(0, 3).map(r => escHtml(r.display_name || r.receiver_webid.slice(-8))).join(", ");
                    el.textContent = readers.length <= 3
                        ? `Seen by ${names}`
                        : `Seen by ${names} +${readers.length - 3} more`;
                    break;
                }
                case "import_complete":
                    showToast(`Import complete: ${event.counts?.messages || 0} messages imported.`);
                    // Reload relationships and rooms
                    socket.send(JSON.stringify({cmd: 'get_relationships'}));
                    break;
            }
        }

        function handleReactionEvent(event, action) {
            const { message_id, emoji, from_webid } = event;
            if (!messageReactions[message_id]) messageReactions[message_id] = {};
            if (!messageReactions[message_id][emoji]) messageReactions[message_id][emoji] = [];
            
            if (action === "add") {
                if (!messageReactions[message_id][emoji].includes(from_webid)) {
                    messageReactions[message_id][emoji].push(from_webid);
                }
            } else {
                messageReactions[message_id][emoji] = messageReactions[message_id][emoji].filter(w => w !== from_webid);
            }
            renderReactions(message_id);
            // Pod: persist reaction state (only the acting user writes to their own pod)
            if (activeView && activeView.type === 'local_room') {
                podWriteReactions(activeView.id, message_id, messageReactions[message_id] || {}).catch(() => {});
            }
        }

        function renderLinkPreview(event) {
            const { message_id, preview } = event;
            const msgEl = document.getElementById(`msg-${message_id}`);
            if (!msgEl) return;
            if (msgEl.querySelector('.link-preview')) return;

            let safeUrl = "";
            try {
                const u = new URL(preview.url);
                if (u.protocol === "https:" || u.protocol === "http:") safeUrl = preview.url;
            } catch (_) {}

            const card = document.createElement("div");
            card.className = "link-preview";
            card.innerHTML = `
                ${preview.image ? `<img src="${escHtml(preview.image)}" onerror="this.style.display='none'">` : ''}
                <div class="preview-info">
                    <div class="preview-url">${escHtml(safeUrl ? new URL(safeUrl).hostname : "")}</div>
                    <div class="preview-title">${safeUrl ? `<a href="${escHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escHtml(preview.title || safeUrl)}</a>` : escHtml(preview.title || "")}</div>
                    ${preview.description ? `<div class="preview-desc">${escHtml(preview.description)}</div>` : ""}
                </div>
            `;
            msgEl.appendChild(card);
        }

        function renderReactions(mid) {
            const container = document.getElementById(`reactions-${mid}`);
            if (!container) return;
            const reacts = messageReactions[mid] || {};
            container.innerHTML = "";
            Object.keys(reacts).forEach(emoji => {
                const count = reacts[emoji].length;
                if (count === 0) return;
                const alreadyReacted = selfWebId && reacts[emoji].includes(selfWebId);
                const span = document.createElement("span");
                span.className = "reaction" + (alreadyReacted ? " active" : "");
                span.innerText = `${emoji} ${count}`;
                span.onclick = () => alreadyReacted ? removeReaction(emoji, mid) : addEmoji(emoji, mid);
                container.appendChild(span);
            });
        }

        /* Confirmation Modal — Replace confirm() dialogs */
        function showConfirm(message, onConfirm, onCancel) {
            let modal = document.getElementById("confirm-modal");
            if (!modal) {
                modal = document.createElement("div");
                modal.id = "confirm-modal";
                modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:3000;" +
                    "display:flex;align-items:center;justify-content:center";
                modal.innerHTML =
                    '<div style="background:#1e293b;padding:clamp(12px,4vw,24px);border-radius:12px;width:min(360px,95vw)">' +
                    '<p id="confirm-msg" style="color:#f8fafc;margin:0 0 16px;font-size:0.95rem"></p>' +
                    '<div style="display:flex;gap:8px;justify-content:flex-end">' +
                    '<button id="confirm-cancel" style="background:#334155;color:#94a3b8;border:none;' +
                    'border-radius:6px;padding:6px 16px;cursor:pointer">Cancel</button>' +
                    '<button id="confirm-ok" style="background:#dc2626;color:#fff;border:none;' +
                    'border-radius:6px;padding:6px 16px;cursor:pointer">Confirm</button>' +
                    '</div></div>';
                document.body.appendChild(modal);
            }
            document.getElementById("confirm-msg").textContent = message;
            document.getElementById("confirm-cancel").onclick = () => { modal.style.display = "none"; if (onCancel) onCancel(); };
            document.getElementById("confirm-ok").onclick = () => { modal.style.display = "none"; onConfirm(); };
            modal.style.display = "flex";
        }

        /* Profile Card (B2) — Show user profile popover on avatar click */
        function showProfileCard(webid, displayName, x, y) {
            if (!webid) return;
            _profileCardActive = webid;

            // Get presence status for this user
            const presenceData = userPresence[webid] || { status: "offline" };
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

            // Position popover near click point
            const card = document.getElementById("profile-card");
            card.classList.add("show");
            card.style.left = Math.min(x, window.innerWidth - 310) + "px";
            card.style.top = Math.min(y, window.innerHeight - 250) + "px";
        }

        function profileCardOpenDM() {
            const webid = _profileCardActive;
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
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({cmd: "resolve_did", did: webid}));
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
            _profileCardActive = null;
        }

        // Close profile card on outside click
        document.addEventListener("click", (e) => {
            const card = document.getElementById("profile-card");
            if (card && !card.contains(e.target) && !e.target.closest("[data-profile-avatar]")) {
                hideProfileCard();
            }
        });

        function handlePresenceUpdate(event) {
            const { webid, status, updated_at } = event;
            if (!webid) return;
            
            // Store presence data
            userPresence[webid] = { status, updated_at };
            
            // Re-render all messages from this user to update presence indicators
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

        function togglePicker(msgId, x, y) {
            const picker = document.getElementById("emoji-picker");
            if (lastEmojiMsgId === msgId && picker.style.display === "grid") {
                picker.style.display = "none";
                return;
            }
            lastEmojiMsgId = msgId;
            picker.style.display = "grid";
            // Clamp to viewport so picker never clips off-screen on mobile
            const pw = picker.offsetWidth || 160;
            const ph = picker.offsetHeight || 120;
            const clampedLeft = Math.max(8, Math.min(x, window.innerWidth - pw - 8));
            const clampedTop = Math.max(8, y - ph - 8);
            picker.style.left = `${clampedLeft}px`;
            picker.style.top = `${clampedTop}px`;
        }

        function addEmoji(emoji, msgId = null) {
            const mid = msgId || lastEmojiMsgId;
            const picker = document.getElementById("emoji-picker");
            picker.style.display = "none";

            const payload = {
                cmd: "add_reaction",
                message_id: mid,
                emoji: emoji
            };
            if (activeView.type === "dm" || activeView.type === "local_dm") payload.cert_id = activeView.id;
            else payload.room_id = activeView.id;

            socket.send(JSON.stringify(payload));
        }

        function removeReaction(emoji, msgId) {
            if (!socket || !activeView) return;
            const payload = {cmd: "remove_reaction", message_id: msgId, emoji: emoji};
            if (activeView.type === "dm" || activeView.type === "local_dm")
                payload.cert_id = activeView.id;
            else
                payload.room_id = activeView.id;
            socket.send(JSON.stringify(payload));
        }

        document.addEventListener("click", (e) => {
            const picker = document.getElementById("emoji-picker");
            if (picker && !picker.contains(e.target) && !e.target.classList.contains("react-btn")) {
                picker.style.display = "none";
            }
        });

        function setReply(msgId) {
            const msg = messageMap[msgId];
            if (!msg) return;
            replyingTo = {
                id: msgId,
                name: msg.from_display_name || msg.from_webid.slice(0, 8),
                content: msg.content.slice(0, 60) + (msg.content.length > 60 ? "..." : "")
            };
            const bar = document.getElementById("reply-bar");
            const text = document.getElementById("reply-text");
            text.innerText = `Replying to ${replyingTo.name}: ${replyingTo.content}`;
            bar.style.display = "flex";
            document.getElementById("message-input").focus();
        }

        function cancelReply() {
            replyingTo = null;
            document.getElementById("reply-bar").style.display = "none";
        }

        function loadLocalHistory(threadId, limit) {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;
            const feed = document.getElementById("message-feed");
            if (!document.getElementById("history-skeleton")) {
                const skel = document.createElement("div");
                skel.id = "history-skeleton";
                skel.innerHTML = `<div class="skeleton-msg"></div>
                    <div class="skeleton-msg short"></div>
                    <div class="skeleton-msg"></div>`;
                feed.appendChild(skel);
            }
            socket.send(JSON.stringify({cmd: "get_local_history", thread_id: threadId, limit: limit || 100}));
        }

        function setPodSyncIndicator(show) {
            const el = document.getElementById("pod-sync-indicator");
            if (el) el.style.display = show ? "" : "none";
        }

        function _injectPodMessage(msg) {
            if (!msg || !msg.message_id) return;
            if (allMessages.find(m => m.message_id === msg.message_id)) return;
            messageMap[msg.message_id] = msg;
            allMessages.push(msg);
            allMessages.sort((a, b) => (a.timestamp || "").localeCompare(b.timestamp || ""));
            renderMessages();
        }

        function loadRoomHistory(threadId, limit) {
            loadLocalHistory(threadId, limit);
            if (!solidSession.info.isLoggedIn) return;
            const now = Date.now();
            const last = _podReadLastFetch[threadId] || 0;
            if ((now - last) < POD_READ_DEBOUNCE_MS) return;
            _podReadLastFetch[threadId] = now;
            setPodSyncIndicator(true);
            podReadMessages(threadId)
                .then((podMsgs) => {
                    setPodSyncIndicator(false);
                    const known = new Set(allMessages.map(m => m.message_id));
                    podMsgs.filter(m => !known.has(m.message_id)).forEach(_injectPodMessage);
                })
                .catch(() => setPodSyncIndicator(false));
        }
        function deleteMsg(msgId) {
            if (!activeView) return;
            showConfirm("Delete this message?", () => {
                socket.send(JSON.stringify({
                    cmd: "delete_local_message",
                    message_id: msgId,
                    thread_id: activeView.id,
                }));
            });
        }

        function toggleSidebar() {
            document.getElementById("sidebar").classList.toggle("active");
            document.getElementById("overlay").classList.toggle("active");
        }

        document.getElementById("overlay").onclick = toggleSidebar;

        function updateSidebarBadge(id) {
            const li = document.getElementById(`nav-${id}`);
            if (li) {
                const count = unreadCounts[id] || 0;
                let badge = li.querySelector(".unread-badge");
                if (count > 0) {
                    if (!badge) {
                        badge = document.createElement("span");
                        badge.className = "unread-badge";
                        li.appendChild(badge);
                    }
                    badge.textContent = count > 99 ? "99+" : String(count);
                    li.style.fontWeight = "600";
                } else {
                    if (badge) badge.remove();
                    li.style.fontWeight = "normal";
                }
            }
            updatePageTitle();
            _syncTrayUnread();
        }

        function updatePageTitle() {
            const total = Object.values(unreadCounts).reduce((a, b) => a + b, 0);
            document.title = total > 0 ? `(${total}) Proxion` : "Proxion";
        }

        // Derive a consistent hue-based background color from a webid string
        function webidColor(webid) {
            let hash = 0;
            for (let i = 0; i < (webid || "").length; i++)
                hash = (Math.imul(hash, 31) + webid.charCodeAt(i)) | 0;
            const hue = Math.abs(hash) % 360;
            return `hsl(${hue}, 55%, 42%)`;
        }

        // Lightweight Markdown renderer (no external deps)
        function renderMarkdown(text) {
            if (!text) return "";
            let s = text.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
            // Code blocks
            s = s.replace(/```([\s\S]*?)```/g, (_, code) =>
                `<pre class="code-block"><code>${code.trim()}</code></pre>`);
            // Inline code
            s = s.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
            // Bold
            s = s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
            s = s.replace(/__(.+?)__/g, '<b>$1</b>');
            // Italic
            s = s.replace(/\*([^*\n]+)\*/g, '<i>$1</i>');
            s = s.replace(/_([^_\n]+)_/g, '<i>$1</i>');
            // Strikethrough
            s = s.replace(/~~(.+?)~~/g, '<s>$1</s>');
            // Newlines (not inside pre blocks)
            s = s.replace(/\n/g, '<br>');
            return s;
        }

        // Scroll-to-bottom button logic
        function scrollToBottom() {
            const feed = document.getElementById("message-feed");
            feed.scrollTop = feed.scrollHeight;
            _scrollBottomUnread = 0;
            document.getElementById("scroll-bottom-btn").style.display = "none";
            if (activeView) _sendUpdateLastRead(activeView.id);
        }

        function maybeShowEmptyState() {
            const feed = document.getElementById("message-feed");
            if (allMessages.length === 0 && !feed.querySelector(".empty-state, .system-msg")) {
                const el = document.createElement("div");
                el.className = "empty-state";
                el.innerHTML = `<div style="opacity:0.3;"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="48" height="48"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.76c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.076-4.076a1.526 1.526 0 0 1 1.037-.443 48.282 48.282 0 0 0 5.68-.494c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"/></svg></div>
                    <div>No messages yet.</div>
                    <div style="font-size:0.85em;color:#64748b;margin-top:4px;">Be the first to say hello.</div>`;
                feed.appendChild(el);
            }
        }

        function _dateLabelForTimestamp(ts) {
            const d = new Date(ts);
            const today = new Date();
            const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
            if (d.toDateString() === today.toDateString()) return "Today";
            if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
            return d.toLocaleDateString(undefined, {month:"long", day:"numeric"});
        }

        function handleTyping(event) {
            const id = event.room_id || event.cert_id;
            if (!activeView || activeView.id !== id) return;
            
            typingUsers[event.from_webid] = Date.now();
            updateTypingDisplay();
        }

        function updateTypingDisplay() {
            const now = Date.now();
            const activeTyping = Object.keys(typingUsers).filter(uid => now - typingUsers[uid] < 4000);
            const el = document.getElementById("typing-indicator");
            if (activeTyping.length > 0) {
                el.innerText = `${activeTyping[0].slice(0, 8)}... is typing...`;
            } else {
                el.innerText = "";
            }
        }
        setInterval(updateTypingDisplay, 1000);

        function populateSidebar(listId, items, type) {
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
                    hideEmptyState();
                    activeView = { type: type, id: id, name: name };
                    document.getElementById("chat-header-name").innerText = (type === "room" ? "# " : "@ ") + name;
                    document.getElementById("message-feed").innerHTML = "";
                    _lastRenderedDate = null;
                    messageMap = {};
                    allMessages = [];
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
                muteIcon.style.cssText = `display:${mutedThreads.has(id) ? "" : "none"};font-size:0.75em;color:#64748b;margin-left:4px;flex-shrink:0;`;
                muteIcon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9.143 17.082a24.248 24.248 0 0 0 3.844.148m-3.844-.148a23.856 23.856 0 0 1-5.455-1.31 8.964 8.964 0 0 0 2.3-5.542m3.155 6.852a3 3 0 0 0 5.667 1.97m1.965-2.277L21 21m-4.225-4.225a23.81 23.81 0 0 0 3.536-1.003 8.967 8.967 0 0 1-2.312-6.022V9A6 6 0 0 0 9.239 3.477L3 3m6.239.477A5.965 5.965 0 0 0 6 9v.75a8.966 8.966 0 0 1-2.312 6.022"/></svg>';
                li.appendChild(muteIcon);
                list.appendChild(li);
                updateSidebarBadge(id); // apply existing unreads
            });
        }

        function updateVoiceChannels(roomId) {
            // Voice channels not yet implemented — keep section hidden
        }

        let _inVoiceChannel = null;

        async function joinVoice(roomId) {
            socket.send(JSON.stringify({cmd: "join_voice_channel", room_id: roomId}));
            _inVoiceChannel = roomId;
            const leaveBtn = document.getElementById("leave-voice-channel-btn");
            if (leaveBtn) leaveBtn.style.display = "";
        }

        function leaveVoiceChannel() {
            if (!_inVoiceChannel) return;
            socket.send(JSON.stringify({cmd: "leave_voice_channel", room_id: _inVoiceChannel}));
            _inVoiceChannel = null;
            const leaveBtn = document.getElementById("leave-voice-channel-btn");
            if (leaveBtn) leaveBtn.style.display = "none";
            // Close all peer connections in the channel
            for (const peerId of Object.keys(peerConnections || {})) {
                try { peerConnections[peerId].close(); } catch (_) {}
                delete peerConnections[peerId];
            }
            showToast("Left voice channel");
        }

        function _expireLabel(msRemaining) {
            if (msRemaining <= 0) return "expired";
            const s = Math.floor(msRemaining / 1000);
            if (s < 60) return `${s}s`;
            const m = Math.floor(s / 60);
            if (m < 60) return `${m}m`;
            const h = Math.floor(m / 60);
            if (h < 24) return `${h}h`;
            return `${Math.floor(h / 24)}d`;
        }

        function timeAgo(date) {
            const seconds = Math.floor((new Date() - new Date(date)) / 1000);
            if (seconds < 60) return "Just now";
            const minutes = Math.floor(seconds / 60);
            if (minutes < 60) return `${minutes}m ago`;
            const hours = Math.floor(minutes / 60);
            if (hours < 24) return `${hours}h ago`;
            return new Date(date).toLocaleDateString();
        }

        function renderMessages() {
            const feed = document.getElementById("message-feed");
            const slice = allMessages.slice(-RENDER_WINDOW);
            feed.innerHTML = "";
            _lastRenderedDate = null;
            _renderThreaded(slice, feed);
            feed.scrollTop = feed.scrollHeight;
        }

        function renderMessage(msg) {
            // Skip DOM work for messages that don't belong to the active thread
            if (activeView && msg.thread_id && msg.thread_id !== activeView.id) return;
            // Push to allMessages array (virtual scroll buffer)
            if (!allMessages.find(m => m.message_id === msg.message_id)) {
                allMessages.push(msg);
            }
            messageMap[msg.message_id] = msg;
            // Only append DOM element if within the render window
            if (allMessages.length <= RENDER_WINDOW || allMessages.indexOf(msg) >= allMessages.length - RENDER_WINDOW) {
                const feed = document.getElementById("message-feed");
                const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
                if (msg.reply_to_id) {
                    _insertReplyInFeed(msg, feed);
                } else {
                    // Root message: prev is the last root-level message visible
                    const visibleMsgs = [...feed.querySelectorAll(".message[data-message-id]")];
                    const lastEl = visibleMsgs[visibleMsgs.length - 1];
                    const prev = lastEl ? messageMap[lastEl.dataset.messageId] : null;
                    msg._threadDepth = 0;
                    _renderMessageEl(msg, feed, prev && (prev._threadDepth || 0) === 0 ? prev : null);
                }
                if (atBottom) {
                    feed.scrollTop = feed.scrollHeight;
                } else {
                    // Scrolled up — show scroll-to-bottom button with unread count
                    _scrollBottomUnread++;
                    const btn = document.getElementById("scroll-bottom-btn");
                    const cnt = document.getElementById("scroll-bottom-count");
                    cnt.textContent = _scrollBottomUnread > 0 ? _scrollBottomUnread : "";
                    btn.style.display = "block";
                }
            }
            // Track last-seen timestamp for the active thread (used for history catch-up)
            if (msg.local && msg.timestamp && activeView && activeView.id === msg.thread_id) {
                const prev = localStorage.getItem("proxion_seen_" + msg.thread_id);
                if (!prev || msg.timestamp > prev) {
                    localStorage.setItem("proxion_seen_" + msg.thread_id, msg.timestamp);
                }
            }
        }

        // Inserts a real-time reply message after the last message in its parent's thread.
        function _insertReplyInFeed(msg, feed) {
            const parentMsg = messageMap[msg.reply_to_id];
            msg._threadDepth = parentMsg ? (parentMsg._threadDepth || 0) + 1 : 1;

            const parentEl = document.getElementById(`msg-${msg.reply_to_id}`);
            if (!parentEl) {
                // Parent not visible — append at end with no grouping context
                _renderMessageEl(msg, feed, null);
                renderReactions(msg.message_id);
                return;
            }

            // Walk forward in the DOM to find the last element that belongs to this thread
            // (i.e., has the same or deeper thread depth as the reply being inserted).
            let insertAfterEl = parentEl;
            let sibling = parentEl.nextElementSibling;
            while (sibling && sibling.classList.contains("message")) {
                const sibDepth = parseInt(sibling.dataset.threadDepth || "0", 10);
                if (sibDepth >= msg._threadDepth) {
                    insertAfterEl = sibling;
                    sibling = sibling.nextElementSibling;
                } else {
                    break;
                }
            }

            // Build the element via a detached container
            const prevMsgId = insertAfterEl.dataset.messageId;
            const prevMsg = prevMsgId ? messageMap[prevMsgId] : null;
            const tempFeed = document.createElement("div");
            _renderMessageEl(msg, tempFeed, prevMsg);
            const newEl = tempFeed.firstElementChild;
            if (!newEl) return;

            const insertBeforeEl = insertAfterEl.nextSibling;
            if (insertBeforeEl) {
                feed.insertBefore(newEl, insertBeforeEl);
            } else {
                feed.appendChild(newEl);
            }
            renderReactions(msg.message_id);
        }

        // Reorders a flat chronological list so each reply immediately follows its parent.
        // Attaches _threadDepth (0 = root, 1 = reply, 2 = reply-to-reply) in-place.
        function _buildThreadedMessages(messages) {
            if (!messages.length) return [];
            const byId = {};
            messages.forEach(m => { byId[m.message_id] = m; });
            const childrenOf = {};
            const roots = [];
            messages.forEach(m => {
                if (m.reply_to_id && byId[m.reply_to_id]) {
                    (childrenOf[m.reply_to_id] = childrenOf[m.reply_to_id] || []).push(m);
                } else {
                    roots.push(m);
                }
            });
            const result = [];
            function flatten(msg, depth) {
                msg._threadDepth = depth;
                result.push(msg);
                (childrenOf[msg.message_id] || []).forEach(child => flatten(child, depth + 1));
            }
            roots.forEach(m => flatten(m, 0));
            return result;
        }

        // Renders `messages` in thread order into `feed`, tracking prev for grouping.
        function _renderThreaded(messages, feed) {
            const threaded = _buildThreadedMessages(messages);
            let prev = null;
            threaded.forEach(msg => { _renderMessageEl(msg, feed, prev); prev = msg; });
        }

        function _renderMessageEl(msg, feed, prevInThread) {
            const existing = document.getElementById(`msg-${msg.message_id}`);
            if (existing) return; // already in DOM

            const msgId = msg.message_id;
            messageMap[msgId] = msg;
            const depth = msg._threadDepth || 0;

            // â"€â"€ Date divider (root messages only) â"€â"€
            if (msg.timestamp && depth === 0) {
                const dateLabel = _dateLabelForTimestamp(msg.timestamp);
                if (dateLabel !== _lastRenderedDate) {
                    _lastRenderedDate = dateLabel;
                    const divEl = document.createElement("div");
                    divEl.className = "date-divider";
                    divEl.innerHTML = `<span>${dateLabel}</span>`;
                    feed.appendChild(divEl);
                }
            }

            // â"€â"€ Message grouping: only group with messages at the same depth â"€â"€
            const isGrouped = prevInThread &&
                msg.from_webid && msg.from_webid !== "unknown" &&
                prevInThread.from_webid === msg.from_webid &&
                (prevInThread._threadDepth || 0) === depth &&
                msg.timestamp && prevInThread.timestamp &&
                (new Date(msg.timestamp) - new Date(prevInThread.timestamp)) < 120000;

            const div = document.createElement("div");
            div.id = `msg-${msgId}`;
            div.setAttribute("data-message-id", msgId);
            div.setAttribute("data-thread-depth", depth);
            div.dataset.fromWebid = msg.from_webid || "";
            div.className = "message" + (isGrouped ? " msg-grouped" : "") + (depth > 0 ? " reply-nested" : "");
            if (msg.is_search_result) div.classList.add("search-match");
            // R11.1.3: expiry tracking
            if (currentDisappearMs > 0 && msg.timestamp) {
                const expiresAt = new Date(msg.timestamp).getTime() + currentDisappearMs;
                div.dataset.expiresAt = String(expiresAt);
            }

            const name = msg.from_display_name || (msg.from_webid || "").slice(0, 12) || (msg.from_pub_hex || "").slice(0, 12);
            const suffix = didSuffix(msg.from_webid || msg.from_pub_hex || "");
            const avatarColor = webidColor(msg.from_webid);

            const presenceData = userPresence[msg.from_webid] || { status: "offline" };
            const presenceClass = presenceData.status === "online" ? "online" :
                                  presenceData.status === "away" ? "away" :
                                  presenceData.status === "busy" ? "busy" : "";

            const avatarBase = msg.from_avatar_b64
                ? `<img src="data:image/png;base64,${msg.from_avatar_b64}" class="avatar" style="width:40px;height:40px;border-radius:50%;">`
                : `<div class="avatar placeholder" style="background:${avatarColor};width:40px;height:40px;line-height:40px;font-size:16px;font-weight:bold;text-align:center;border-radius:50%;">${(name[0] || "?").toUpperCase()}</div>`;
            const presenceDot = `<div class="avatar-presence ${presenceClass}" title="${presenceData.status}" style="bottom:-1px;right:-1px;"></div>`;
            const avatarHtml = `<div style="position:relative;display:inline-block;cursor:pointer;" data-profile-avatar data-msg-action="profile" data-webid="${msg.from_webid}" data-name="${name.replace(/"/g,'&quot;')}">${avatarBase}${presenceDot}</div>`;

            // Render text with Markdown and mention highlighting
            let rawText = msg.snippet || msg.content || "";
            const selfDisplayName = localStorage.getItem("proxion_display_name") || "";
            const mentionsMe = (msg.mentions && selfWebId && msg.mentions.includes(selfWebId)) ||
                (selfDisplayName && rawText.toLowerCase().includes("@" + selfDisplayName.toLowerCase()));
            if (mentionsMe) div.classList.add("mention-highlight");
            let renderedText = renderMarkdown(rawText).replace(/@(\w+)/g, (match, uname) =>
                `<span class="${selfDisplayName && uname.toLowerCase() === selfDisplayName.toLowerCase() ? "mention mention-self" : "mention"}">@${uname}</span>`
            );

            let fileHtml = "";
            if (msg.file) {
                // Strip path-traversal sequences before using filename in download attribute.
                // escHtml handles XSS; this strips directory components so the OS/browser
                // cannot be confused into writing outside the Downloads folder.
                const _rawFilename = (msg.file.filename || 'file')
                    .replace(/[/\\]/g, '')       // remove / and \
                    .replace(/\.\./g, '')         // remove ..
                    .trim() || 'file';
                const safeFilename = escHtml(_rawFilename);
                const _mime = (msg.file.mime_type || '').toLowerCase();
                const _IMAGE_TYPES = new Set(['image/jpeg','image/png','image/gif','image/webp','image/avif']);
                if (_IMAGE_TYPES.has(_mime) && msg.file.data_b64) {
                    // R13.7: inline image preview
                    const _imgSrc = `data:${_mime};base64,${msg.file.data_b64}`;
                    fileHtml = `<div class="attachment">
                        <img class="msg-image-preview" src="${_imgSrc}" alt="${safeFilename}" loading="lazy">
                        <a href="data:application/octet-stream;base64,${msg.file.data_b64}" download="${safeFilename}"
                           style="color:#e94560;font-size:0.8em;display:block;margin-top:3px;">Download ${safeFilename}</a></div>`;
                } else {
                    // Force octet-stream to prevent data URI MIME injection
                    fileHtml = `<div class="attachment"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="18" height="18"><path stroke-linecap="round" stroke-linejoin="round" d="m18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13"/></svg> ${safeFilename} (${Math.round(msg.file.size/1024)} KB)
                        <a href="data:application/octet-stream;base64,${msg.file.data_b64}" download="${safeFilename}"
                           style="color:#e94560;margin-left:10px;">Download</a></div>`;
                }
            }

            const exactTs = msg.timestamp ? new Date(msg.timestamp).toLocaleString() : "";
            const compactTime = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"}) : "";

            const isOwn = (msg.own === true) ||
                (selfWebId && msg.from_webid === selfWebId) ||
                (selfPubHex && msg.from_pub_hex === selfPubHex);

            const editBtn = isOwn
                ? `<button data-msg-action="edit" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.78rem;" title="Edit"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"/></svg></button>`
                : "";
            const deleteBtn = isOwn && (msg.local || activeView?.local)
                ? `<button data-msg-action="delete" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.78rem;" title="Delete"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg></button>`
                : "";
            const forwardBtn = `<button data-msg-action="forward" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.78rem;" title="Forward">&#8599;</button>`;

            // â"€â"€ Avatar column â"€â"€
            const avatarCol = document.createElement("div");
            avatarCol.className = "msg-avatar-col";
            avatarCol.innerHTML = isGrouped
                ? `<span class="msg-compact-ts" title="${exactTs}">${compactTime}</span>`
                : avatarHtml;

            // â"€â"€ Body column â"€â"€
            const body = document.createElement("div");
            body.className = "msg-body";

            // Inline reply context (Discord-style)
            if (msg.reply_to_id) {
                const parent = messageMap[msg.reply_to_id];
                if (parent) {
                    const parentName = parent.from_display_name || (parent.from_webid || "").slice(0, 8);
                    const parentSnippet = (parent.content || "").slice(0, 50) + (parent.content && parent.content.length > 50 ? "…" : "");
                    body.innerHTML += `<div class="reply-context" data-msg-action="scroll-reply" data-reply-id="${msg.reply_to_id}" style="cursor:pointer;"><span class="reply-connector"></span><b style="color:${webidColor(parent.from_webid)};margin-right:2px;">${escHtml(parentName)}</b><span>${parentSnippet.replace(/</g,"&lt;")}</span></div>`;
                } else {
                    // Parent not in window — fetch it, render quote when it arrives
                    const placeholder = document.createElement("div");
                    placeholder.className = "reply-context reply-context-loading";
                    placeholder.dataset.replyTarget = msg.reply_to_id;
                    placeholder.innerHTML = `<span class="reply-connector"></span><em style="color:#64748b">Loading reply context…</em>`;
                    body.appendChild(placeholder);
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({ cmd: "get_message", message_id: msg.reply_to_id }));
                    }
                }
            }

            // Round 68: forwarded banner
            if (msg.forwarded) {
                body.innerHTML += `<div class="forwarded-banner">↗ Forwarded from ${escHtml(msg.forwarded_from_name || '')}</div>`;
            }

            // Header: name + timestamp (first in group only)
            if (!isGrouped) {
                const suffixHtml = suffix ? `<span style="font-size:0.72em;color:#475569;margin-left:4px;font-weight:400;">·${suffix}</span>` : "";
                const botBadge = msg.is_bot ? `<span class="bot-badge">BOT</span>` : "";
                const importedBadge = msg.imported ? `<span style="font-size:0.7em;color:#94a3b8;background:#1e293b;border:1px solid #334155;border-radius:3px;padding:1px 5px;margin-left:6px;vertical-align:middle;">Imported</span>` : "";
                // R11.2.3: unverified shield for DID contacts not yet verified
                const isVerified = !msg.from_webid || msg.from_webid === selfWebId ||
                    localStorage.getItem("proxion_verified_" + msg.from_webid) === "1";
                const shieldHtml = (!isVerified && msg.from_webid && msg.from_webid.startsWith("did:key:"))
                    ? `<span title="Identity not verified — check safety number" style="color:#475569;margin-left:4px;font-size:0.85em;">&#x1F6E1;</span>`
                    : "";
                // R11.1.3: expiry countdown label
                let expireHtml = "";
                if (currentDisappearMs > 0 && msg.timestamp) {
                    const expiresAt = new Date(msg.timestamp).getTime() + currentDisappearMs;
                    expireHtml = `<span class="msg-expire-countdown" style="font-size:0.7em;color:#475569;margin-left:6px;" title="Expires">⏱ ${_expireLabel(expiresAt - Date.now())}</span>`;
                }
                body.innerHTML += `<div class="msg-header"><span class="msg-sender" style="color:${avatarColor}">${escHtml(name)}${botBadge}${suffixHtml}${shieldHtml}</span><span class="msg-ts-header" title="${exactTs}">${timeAgo(msg.timestamp)}${importedBadge}${expireHtml}</span></div>`;
            }

            // Content
            const editedHtml = msg.edited_at
                ? `<span class="edited-badge" role="button" tabindex="0" data-msg-id="${msgId}" title="Show edit history">(edited)</span>`
                : "";
            if (msg.content_type === "audio" && msg.audio_b64) {
                const dur = msg.duration_ms ? `<span class="audio-duration">${Math.round(msg.duration_ms/1000)}s</span>` : "";
                body.innerHTML += `<div class="audio-message"><audio controls src="data:audio/webm;base64,${msg.audio_b64}"></audio>${dur}</div>`;
            } else {
                body.innerHTML += `<div class="msg-content"><span class="msg-text">${renderedText}</span>${editedHtml}</div>`;
            }

            if (fileHtml) body.innerHTML += fileHtml;
            body.innerHTML += `<div id="reactions-${msgId}" class="reactions"></div>`;
            if (isOwn) body.innerHTML += `<span class="read-receipt" data-msg-id="${msgId}">&#10003;</span>`;

            // Hover action bar
            body.innerHTML += `<div class="msg-actions">
                <button data-msg-action="react" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.8rem;" title="React">+</button>
                <button data-msg-action="reply" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.85rem;" title="Reply"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3"/></svg></button>
                ${editBtn}${deleteBtn}${forwardBtn}
                <button data-msg-action="pin" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.78rem;" title="Pin"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0 1 11.186 0Z"/></svg></button>
            </div>`;

            div.appendChild(avatarCol);
            div.appendChild(body);
            div.addEventListener("contextmenu", e => openCtxMenu(e, msgId));
            feed.appendChild(div);
            renderReactions(msgId);
        }

        // Virtual scroll + persistent history: load earlier messages on scroll to top
        let _loadingOlderHistory = false;
        document.getElementById("message-feed").addEventListener("scroll", (e) => {
            const feed = e.target;
            // Hide scroll-to-bottom btn when user scrolls to bottom
            if (feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60) {
                _scrollBottomUnread = 0;
                document.getElementById("scroll-bottom-btn").style.display = "none";
            }
            if (feed.scrollTop !== 0) return;
            // First expand in-memory buffer
            if (allMessages.length > RENDER_WINDOW) {
                const feed = e.target;
                const rendered = feed.querySelectorAll(".message").length;
                const totalLoaded = rendered + SCROLL_BATCH;
                const slice = allMessages.slice(-Math.min(totalLoaded, allMessages.length));
                feed.innerHTML = "";
                _lastRenderedDate = null;
                _renderThreaded(slice, feed);
                feed.scrollTop = 10;
                return;
            }
            // Then fetch older messages from DB
            const _isCertDm = activeView && activeView.type === "dm";
            if (activeView && (activeView.local || _isCertDm) && !_loadingOlderHistory
                    && socket && socket.readyState === WebSocket.OPEN) {
                const oldest = allMessages[0];
                if (oldest && oldest.timestamp) {
                    _loadingOlderHistory = true;
                    if (_isCertDm) {
                        socket.send(JSON.stringify({
                            cmd: "read_dm",
                            cert_id: activeView.certId,
                            before_timestamp: oldest.timestamp,
                            limit: 50,
                        }));
                    } else {
                        socket.send(JSON.stringify({
                            cmd: "get_local_history",
                            thread_id: activeView.id,
                            before_timestamp: oldest.timestamp,
                            limit: 50,
                        }));
                    }
                }
            }
        });


        // -- Round 60: Read receipt helpers --
        function updateReadReceipt(msgId, readers) {
            const el = document.querySelector(`.read-receipt[data-msg-id="${msgId}"]`);
            if (!el) return;
            const others = (readers || []).filter(w => w !== selfWebId);
            if (!others.length) return;
            el.textContent = '✓✓';
            el.classList.add('read');
            el.title = 'Read by: ' + others.slice(0,5).join(', ');
        }

        // -- Round 65: Disappear banner --
        function updateDisappearBanner(ms) {
            currentDisappearMs = ms || 0;
            const banner = document.getElementById('disappear-banner');
            const label = document.getElementById('disappear-label');
            if (!banner || !label) return;
            if (!ms) { banner.classList.remove('active'); return; }
            const labels = {30000:'30 seconds',300000:'5 minutes',3600000:'1 hour',86400000:'1 day',604800000:'1 week'};
            label.textContent = labels[ms] || (ms/1000 + 's');
            banner.classList.add('active');
        }

        // R11.1.3: 60s sweep — remove messages that have passed their expiry time from the DOM
        setInterval(() => {
            const now = Date.now();
            document.querySelectorAll('.message[data-expires-at]').forEach(el => {
                const exp = parseInt(el.dataset.expiresAt, 10);
                if (exp && now >= exp) {
                    const msgId = el.dataset.messageId;
                    el.remove();
                    if (msgId) {
                        allMessages = allMessages.filter(m => m.message_id !== msgId);
                        delete messageMap[msgId];
                    }
                } else if (exp) {
                    const countdownEl = el.querySelector('.msg-expire-countdown');
                    if (countdownEl) countdownEl.textContent = _expireLabel(exp - now);
                }
            });
        }, 60000);

        // -- Round 62: Voice recording --
        let _mediaRecorder = null, _recordingChunks = [], _recordingTimerInterval = null, _recordingSeconds = 0;
        function startVoiceRecording() {
            navigator.mediaDevices.getUserMedia({audio:true}).then(stream => {
                _recordingChunks = []; _recordingSeconds = 0;
                _mediaRecorder = new MediaRecorder(stream, {mimeType:'audio/webm'});
                _mediaRecorder.ondataavailable = e => { if(e.data.size>0) _recordingChunks.push(e.data); };
                _mediaRecorder.onstop = sendVoiceMessage;
                _mediaRecorder.start();
                document.getElementById('voice-recording-bar')?.classList.add('active');
                document.getElementById('voice-record-btn')?.classList.add('recording');
                _recordingTimerInterval = setInterval(() => {
                    _recordingSeconds++;
                    const t = document.getElementById('recording-timer');
                    if (t) t.textContent = Math.floor(_recordingSeconds/60)+':'+String(_recordingSeconds%60).padStart(2,'0');
                    if (_recordingSeconds >= 60) stopVoiceRecording(false);
                }, 1000);
            }).catch(() => showToast('Microphone access denied', 'error'));
        }
        function stopVoiceRecording(send=true) {
            clearInterval(_recordingTimerInterval);
            document.getElementById('voice-recording-bar')?.classList.remove('active');
            document.getElementById('voice-record-btn')?.classList.remove('recording');
            if (_mediaRecorder && _mediaRecorder.state !== 'inactive') {
                _mediaRecorder._sendOnStop = send;
                _mediaRecorder.stop();
            }
        }
        function sendVoiceMessage() {
            if (!_mediaRecorder || !_mediaRecorder._sendOnStop || !activeView || !socket) return;
            const blob = new Blob(_recordingChunks, {type:'audio/webm'});
            const voiceMsgId = crypto.randomUUID ? crypto.randomUUID() : (Date.now().toString(36));
            const reader2 = new FileReader();
            reader2.onloadend = () => {
                socket.send(JSON.stringify({
                    cmd:'send_voice_message', thread_id:activeView.id,
                    audio_b64:reader2.result.split(',')[1], duration_ms:_recordingSeconds*1000,
                    message_id: voiceMsgId,
                }));
                if (activeView.type === 'local_room') {
                    podUploadVoiceAudio(activeView.id, voiceMsgId, blob).catch(() => {});
                }
            };
            reader2.readAsDataURL(blob);
        }

        // -- Round 67: Screen sharing --
        let _screenStream = null, _isSharing = false;
        async function startScreenShare() {
            if (_isSharing || !pc) return;
            try {
                _screenStream = await navigator.mediaDevices.getDisplayMedia({video:{cursor:'always'},audio:false});
            } catch { showToast('Screen share cancelled'); return; }
            _isSharing = true;
            const screenTrack = _screenStream.getVideoTracks()[0];
            const sender = pc.getSenders().find(s => s.track?.kind === 'video');
            if (sender) sender.replaceTrack(screenTrack);
            else pc.addTrack(screenTrack, _screenStream);
            screenTrack.onended = () => stopScreenShare();
            const sBtn = document.getElementById('screenshare-btn');
            if (sBtn) sBtn.classList.add('vw-sharing');
            if (socket && currentCall) socket.send(JSON.stringify({cmd:'screenshare_started',session_id:currentCall.session_id||''}));
        }
        function stopScreenShare() {
            _isSharing = false;
            _screenStream?.getTracks().forEach(t => t.stop());
            _screenStream = null;
            const sBtn = document.getElementById('screenshare-btn');
            if (sBtn) sBtn.classList.remove('vw-sharing');
            if (socket && currentCall) socket.send(JSON.stringify({cmd:'screenshare_stopped',session_id:currentCall.session_id||''}));
        }

        // -- Round 68: Forward modal --
        let _forwardingMsgId = null;
        function openForwardModal(msgId) {
            _forwardingMsgId = msgId;
            const modal = document.getElementById('forward-modal');
            const list = document.getElementById('forward-thread-list');
            if (!modal || !list) return;
            const threads = [];
            document.querySelectorAll('[data-room-id]').forEach(el => {
                const name = el.querySelector('.room-name')?.textContent || el.dataset.roomId;
                threads.push({id:el.dataset.roomId, name});
            });
            if (!threads.length) { list.innerHTML = '<em style="color:#94a3b8">No rooms to forward to.</em>'; }
            else {
                list.innerHTML = '';
                threads.forEach(t => {
                    const item = document.createElement('div');
                    item.className = 'forward-thread-item';
                    item.textContent = t.name;
                    item.addEventListener('click', () => {
                        if (socket && _forwardingMsgId) {
                            socket.send(JSON.stringify({cmd:'forward_message',message_id:_forwardingMsgId,target_thread_id:t.id}));
                        }
                        modal.style.display = 'none';
                    });
                    list.appendChild(item);
                });
            }
            modal.style.display = 'flex';
        }

        // -- Round 69: Schedule picker --
        function openSchedulePicker() {
            const p = document.getElementById('schedule-picker');
            if (p) p.style.display = (p.style.display === 'none' || !p.style.display) ? 'flex' : 'none';
        }

        // -- Round 70: Integrations panel --
        function openIntegrationsPanel() {
            if (!activeView || !socket) return;
            socket.send(JSON.stringify({cmd:'list_webhooks', thread_id:activeView.id}));
            const existing = document.getElementById('integrations-modal');
            if (existing) existing.remove();
            const modal = document.createElement('div');
            modal.id = 'integrations-modal';
            modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1003;display:flex;align-items:center;justify-content:center;';
            const box = document.createElement('div');
            box.style.cssText = 'background:#1e293b;border-radius:8px;padding:20px;min-width:340px;color:#f1f5f9;';
            box.innerHTML = '<h3 style="margin:0 0 12px">Room Integrations</h3>' +
                '<div id="webhook-list-area" style="margin-bottom:12px;min-height:40px;"></div>' +
                '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">' +
                '<button id="ci-incoming-btn" style="background:var(--accent,#e94560);border:none;color:#fff;padding:7px 14px;border-radius:4px;cursor:pointer">+ Incoming Webhook</button>' +
                '<button id="ci-outgoing-btn" style="background:#334155;border:none;color:#f1f5f9;padding:7px 14px;border-radius:4px;cursor:pointer">+ Outgoing Webhook</button>' +
                '</div><button id="ci-close-btn" style="background:#334155;border:none;color:#f1f5f9;padding:7px 14px;border-radius:4px;cursor:pointer">Close</button>';
            modal.appendChild(box);
            document.body.appendChild(modal);
            box.querySelector('#ci-close-btn').addEventListener('click', () => modal.remove());
            box.querySelector('#ci-incoming-btn').addEventListener('click', () => {
                const name = prompt('Bot display name:', 'Bot') || 'Bot';
                sendCmd('create_webhook', {thread_id:activeView.id, direction:'incoming', bot_name:name});
                modal.remove();
            });
            box.querySelector('#ci-outgoing-btn').addEventListener('click', () => {
                const url = prompt('Target HTTPS URL:');
                if (!url || !url.startsWith('https://')) { showToast('Must be HTTPS', 'error'); return; }
                sendCmd('create_webhook', {thread_id:activeView.id, direction:'outgoing', url, bot_name:'Bot'});
                modal.remove();
            });
        }

        function renderSearchResults(event) {
            const feed = document.getElementById("message-feed");
            feed.innerHTML = `<div class="system-msg">Search results for "${escHtml(event.query)}":</div>`;
            if (event.results.length === 0) {
                feed.innerHTML += '<div class="system-msg">No matches found.</div>';
            }
            event.results.forEach(res => {
                renderMessage({...res, is_search_result: true});
            });
        }

        // Search debouncing
        let searchTimeout = null;
        document.getElementById("search-input").onkeyup = (e) => {
            clearTimeout(searchTimeout);
            const query = e.target.value.trim();
            if (query.length < 3) return;
            searchTimeout = setTimeout(() => {
                socket.send(JSON.stringify({cmd: "search", query: query}));
            }, 500);
        };

        let typingThrottled = false;
        document.getElementById("message-input").addEventListener("input", (e) => {
            if (!socket || !activeView || typingThrottled) return;
            const payload = { cmd: "typing" };
            if (activeView.type === "dm" || activeView.type === "local_dm") payload.cert_id = activeView.id;
            else payload.room_id = activeView.id;
            socket.send(JSON.stringify(payload));
            typingThrottled = true;
            setTimeout(() => { typingThrottled = false; }, 3000);
        });

        function _callerDisplayName(webid) {
            const dmPeer = Object.values(localDmPeers).find(p => p.peer_webid === webid);
            if (dmPeer && dmPeer.display_name) return dmPeer.display_name;
            const member = currentRoomMembers.find(m => m.webid === webid);
            if (member && member.display_name) return member.display_name;
            return webid.slice(0, 28);
        }

        function showVoiceBanner(invite) {
            currentCall = invite;
            setCallState(CallState.RINGING);
            const banner = document.getElementById("voice-banner");
            document.getElementById("voice-msg").innerText =
                `Incoming call from ${_callerDisplayName(invite.caller_webid)}`;
            banner.style.display = "flex";
            playRingTone();
            setTimeout(() => {
                if (_callState === CallState.RINGING) {
                    banner.style.display = "none";
                    currentCall = null;
                    setCallState(CallState.IDLE);
                }
            }, 30000);
        }

        async function getMedia() {
            if (!localStream) {
                try {
                    localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
                } catch (err) {
                    showToast("Could not access microphone: " + err, "error");
                }
            }
            return localStream;
        }

        async function getTurnCredentials(username, secret) {
            const enc = new TextEncoder();
            const key = await crypto.subtle.importKey(
                "raw", enc.encode(secret),
                { name: "HMAC", hash: "SHA-1" },
                false, ["sign"]
            );
            const signature = await crypto.subtle.sign("HMAC", key, enc.encode(username));
            const b64 = btoa(String.fromCharCode(...new Uint8Array(signature)));
            return b64;
        }

        function setCallState(newState) {
            const valid = {
                [CallState.IDLE]: [CallState.CALLING, CallState.RINGING],
                [CallState.CALLING]: [CallState.CONNECTED, CallState.ENDING, CallState.IDLE],
                [CallState.RINGING]: [CallState.CONNECTED, CallState.ENDING, CallState.IDLE],
                [CallState.CONNECTED]: [CallState.ENDING],
                [CallState.ENDING]: [CallState.IDLE],
            };
            if (!valid[_callState]?.includes(newState)) return;
            _callState = newState;
            _updateCallUI();
        }

        function _updateCallUI() {
            const widget = document.getElementById("voice-widget");
            if (!widget) return;
            const connected = _callState === CallState.CONNECTED;
            const active = connected
                || _callState === CallState.CALLING
                || _callState === CallState.RINGING;
            widget.style.display = active ? "flex" : "none";
            const ssBtn = document.getElementById("screenshare-btn");
            if (ssBtn) ssBtn.style.display = connected ? "flex" : "none";
            if (!connected && _isSharing) stopScreenShare();
            const statusEl = document.getElementById("vw-status");
            if (statusEl && !connected) {
                statusEl.textContent = _callState === CallState.CALLING ? "Calling..." : "Incoming...";
            }
        }

        function _startCallTimeout() {
            if (_callTimeoutId) clearTimeout(_callTimeoutId);
            _callTimeoutId = setTimeout(() => {
                if (_callState === CallState.CALLING) {
                    showToast("Call not answered");
                    _doHangup();
                }
            }, CALL_TIMEOUT_MS);
        }

        function _clearCallTimeout() {
            if (_callTimeoutId) clearTimeout(_callTimeoutId);
            _callTimeoutId = null;
        }

        async function _setRemoteAndDrainCandidates(sdp, type) {
            await pc.setRemoteDescription({ type, sdp });
            _remoteDescSet = true;
            for (const c of _pendingCandidates) {
                socket.send(JSON.stringify({
                    cmd: "ice_candidate",
                    cert_id: activeView?.id,
                    session_id: currentCallSessionId,
                    candidate: c.candidate,
                    sdp_mid: c.sdpMid,
                    sdp_mline_index: c.sdpMLineIndex
                }));
            }
            _pendingCandidates = [];
        }

        async function initWebRTC(certId, sessionId, isCaller = false, sdpOffer = null) {
            let iceServers = [{ urls: 'stun:stun.l.google.com:19302' }];
            // Fetch TURN credentials from gateway endpoint if not yet loaded
            if (!_turnIceServer) {
                try {
                    const _tr = await fetch('/turn-credentials');
                    const _tc = await _tr.json();
                    if (_tc && _tc.urls && _tc.urls.length > 0) {
                        _turnIceServer = { urls: _tc.urls, username: _tc.username, credential: _tc.credential };
                    }
                } catch (_) {}
            }
            if (_turnIceServer) {
                // Server-computed time-limited credentials (preferred)
                iceServers.push(_turnIceServer);
            } else if (turnUrl && turnSecret) {
                // Legacy: client-side HMAC credential generation
                const timestamp = Math.floor(Date.now() / 1000) + 86400;
                const username = `${timestamp}:${selfWebId}`;
                const credential = await getTurnCredentials(username, turnSecret);
                iceServers.push({ urls: turnUrl, username, credential });
            }
            pc = new RTCPeerConnection({ iceServers: iceServers });
            _pendingCandidates = [];
            _remoteDescSet = false;
            
            const stream = await getMedia();
            if (stream) {
                stream.getTracks().forEach(track => pc.addTrack(track, stream));
            }

            pc.ontrack = (event) => {
                console.log("Remote track received");
                const remoteAudio = new Audio();
                remoteAudio.srcObject = event.streams[0];
                remoteAudio.play();
                setCallState(CallState.CONNECTED);
                const peerName = activeView ? (activeView.name || activeView.id || "") : "";
                document.getElementById("vw-peer-name").textContent = peerName || "";
            };

            pc.onicecandidate = (e) => {
                if (e.candidate) {
                    if (_remoteDescSet) {
                        socket.send(JSON.stringify({
                            cmd: "ice_candidate",
                            cert_id: certId,
                            session_id: sessionId,
                            candidate: e.candidate.candidate,
                            sdp_mid: e.candidate.sdpMid,
                            sdp_mline_index: e.candidate.sdpMLineIndex
                        }));
                    } else {
                        _pendingCandidates.push(e.candidate);
                    }
                }
            };

            if (isCaller) {
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                currentCallSessionId = sessionId;
                setCallState(CallState.CALLING);
                _startCallTimeout();
                socket.send(JSON.stringify({
                    cmd: "voice_invite",
                    cert_id: certId,
                    session_id: sessionId,
                    target_webid: activeView ? activeView.peerWebid : null,
                    sdp_offer: offer.sdp
                }));
            } else if (sdpOffer) {
                await _setRemoteAndDrainCandidates(sdpOffer, 'offer');
                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);
                currentCallSessionId = sessionId;
                socket.send(JSON.stringify({
                    cmd: "voice_answer",
                    cert_id: certId,
                    session_id: sessionId,
                    sdp_answer: answer.sdp
                }));
                setCallState(CallState.CONNECTED);
                startCallTimer();
            }
        }

        function startCallTimer() {
            callStartTime = Date.now();
            if (callTimerInterval) clearInterval(callTimerInterval);
            callTimerInterval = setInterval(() => {
                const s = Math.floor((Date.now() - callStartTime) / 1000);
                const mm = String(Math.floor(s / 60)).padStart(2, "0");
                const ss = String(s % 60).padStart(2, "0");
                const statusEl = document.getElementById("vw-status");
                if (statusEl) statusEl.textContent = `In Call ${mm}:${ss}`;
            }, 1000);
        }

        function stopCallTimer() {
            if (callTimerInterval) { clearInterval(callTimerInterval); callTimerInterval = null; }
            callStartTime = null;
        }

        function stopRingTone() {
            if (ringOscillator) { try { ringOscillator.stop(); } catch(e) {} ringOscillator = null; }
        }

        function playRingTone() {
            stopRingTone();
            try {
                const ctx = new AudioContext();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.connect(gain); gain.connect(ctx.destination);
                osc.frequency.value = 440; gain.gain.value = 0.08;
                osc.start();
                ringOscillator = osc;
                setTimeout(() => stopRingTone(), 30000); // auto-stop after 30s
            } catch(e) { console.warn("Ring tone failed", e); }
        }

        function hangupCleanup() {
            if (pc) { pc.close(); pc = null; }
            if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
            stopCallTimer();
            stopRingTone();
            _clearCallTimeout();
            _pendingCandidates = [];
            _remoteDescSet = false;
            isMuted = false;
            currentCallSessionId = null;
            setCallState(CallState.IDLE);
            const muteBtn = document.getElementById("mute-btn");
            if (muteBtn) muteBtn.classList.remove("vw-muted");
            const vwPeer = document.getElementById("vw-peer-name");
            if (vwPeer) vwPeer.textContent = "";
            const vwStatus = document.getElementById("vw-status");
            if (vwStatus) vwStatus.textContent = "In Call";
        }

        function handleVoiceHangup(event) {
            if (_callState !== CallState.IDLE) setCallState(CallState.ENDING);
            hangupCleanup();
        }

        // Dispatch cross-gateway voice signals relayed via HTTP relay
        function handleVoiceSignalRelay(event) {
            const st = event.signal_type;
            const sd = event.signal_data || {};
            const merged = { session_id: event.session_id, from_webid: event.from_webid, ...sd };
            if (st === "answer") handleVoiceAnswer(merged);
            else if (st === "ice_candidate") handleIceCandidate(merged);
            else if (st === "hangup") handleVoiceHangup(merged);
            else if (st === "offer") showVoiceBanner({ ...merged, caller_webid: event.from_webid, sdp_offer: sd.sdp_offer });
        }

        // Group call: existing member joined before us — they will send us a voice_invite
        function handleVoicePeerPresent(event) {
            showToast(`${event.peer_webid.slice(0, 20)} is in the voice channel`, "info");
            // If the existing peer is remote, we should send them an offer via relay
            if (event.gateway_url && activeView) {
                socket.send(JSON.stringify({
                    cmd: "voice_invite",
                    target_webid: event.peer_webid,
                    session_id: `ch-${event.channel_id}-${Date.now()}`,
                    sdp_offer: null,  // gateway will initiate WebRTC offer toward target
                    channel_id: event.channel_id,
                }));
            }
        }

        // Group call: a new peer joined after us — we should call them
        function handleVoicePeerJoined(event) {
            showToast(`${event.peer_webid.slice(0, 20)} joined the voice channel`, "info");
            if (event.gateway_url) {
                // Remote peer — initiate a relayed call toward them
                if (activeView) {
                    socket.send(JSON.stringify({
                        cmd: "voice_invite",
                        target_webid: event.peer_webid,
                        session_id: `ch-${event.channel_id || activeView.id}-${Date.now()}`,
                        channel_id: event.channel_id || activeView.id,
                    }));
                }
            } else {
                // Local peer — existing mesh path
                if (activeView) initWebRTC(activeView.id, null, true).catch(console.warn);
            }
        }

        // Group call: a peer left
        function handleVoicePeerLeft(event) {
            showToast(`${event.peer_webid.slice(0, 20)} left the voice channel`, "info");
            if (peerConnections?.[event.peer_webid]) {
                try { peerConnections[event.peer_webid].close(); } catch (_) {}
                delete peerConnections[event.peer_webid];
            }
        }

        // peer_discovered response handler — used by the Add Contact modal
        let _peerDiscoveredResolve = null;
        function handlePeerDiscovered(event) {
            if (_peerDiscoveredResolve) {
                _peerDiscoveredResolve(event);
                _peerDiscoveredResolve = null;
            }
        }

        async function handleVoiceAnswer(event) {
            if (pc) {
                await _setRemoteAndDrainCandidates(event.sdp_answer, 'answer');
                _clearCallTimeout();
                setCallState(CallState.CONNECTED);
                startCallTimer();
            }
        }

        async function handleIceCandidate(event) {
            if (pc) {
                try {
                    await pc.addIceCandidate({
                        candidate: event.candidate,
                        sdpMid: event.sdp_mid,
                        sdpMLineIndex: event.sdp_mline_index
                    });
                } catch (e) { console.warn("ICE error", e); }
            }
        }

        document.getElementById("start-call-btn").onclick = async () => {
            if (!activeView || (activeView.type !== "dm" && activeView.type !== "local_dm")) return;
            await initWebRTC(activeView.id, null, true);
        };

        document.getElementById("voice-answer").onclick = async () => {
            if (!currentCall) return;
            stopRingTone();
            const certId = currentCall.cert_id || (activeView ? activeView.id : "");
            await initWebRTC(certId, currentCall.session_id, false, currentCall.sdp_offer);
            document.getElementById("voice-banner").style.display = "none";
        };

        document.getElementById("voice-decline").onclick = () => {
            stopRingTone();
            document.getElementById("voice-banner").style.display = "none";
            currentCall = null;
            setCallState(CallState.IDLE);
        };

        function _doHangup() {
            setCallState(CallState.ENDING);
            if (currentCallSessionId && socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({cmd: "voice_hangup", session_id: currentCallSessionId}));
            }
            hangupCleanup();
        }

        document.getElementById("end-call").onclick = () => {
            _doHangup();
        };

        document.getElementById("mute-btn").onclick = () => {
            if (!localStream) return;
            isMuted = !isMuted;
            localStream.getAudioTracks().forEach(t => { t.enabled = !isMuted; });
            document.getElementById("mute-btn").classList.toggle("vw-muted", isMuted);
        };

        // --------------- Edit message ---------------
        function startEdit(msgId) {
            const msgEl = document.getElementById(`msg-${msgId}`);
            if (!msgEl) return;
            const textEl = msgEl.querySelector(".msg-text");
            if (!textEl) return;
            const original = textEl.innerText;
            editingMsgId = msgId;
            const inp = document.createElement("input");
            inp.type = "text";
            inp.value = original;
            inp.style.cssText = "width:60%;padding:4px;border-radius:4px;border:1px solid #555;background:#0f172a;color:#f1f5f9;font-size:0.95em;";
            const confirmBtn = document.createElement("button");
            confirmBtn.innerText = "✓";
            confirmBtn.className = "edit-confirm-btn";
            confirmBtn.style.cssText = "background:transparent;border:none;cursor:pointer;font-size:1em;margin-left:4px;";
            confirmBtn.onclick = () => commitEdit(msgId, inp.value);
            inp.onkeydown = (e) => {
                if (e.key === "Enter") { e.preventDefault(); commitEdit(msgId, inp.value); }
                if (e.key === "Escape") { cancelEdit(msgId, original); }
            };
            textEl.replaceWith(inp);
            (inp.closest(".msg-body") || msgEl).appendChild(confirmBtn);
            inp.focus();
        }

        function commitEdit(msgId, newContent) {
            if (!socket || !activeView || !newContent.trim()) return;
            const isLocal = activeView.local || activeView.type === "local_room" || activeView.type === "local_dm";
            let payload;
            if (isLocal) {
                payload = { cmd: "edit_local_message", message_id: msgId, thread_id: activeView.id, content: newContent.trim(), from_webid: clientDid };
            } else {
                payload = { cmd: "edit_message", message_id: msgId, content: newContent.trim() };
                if (activeView.type === "dm") payload.cert_id = activeView.id;
                else payload.room_id = activeView.id;
            }
            socket.send(JSON.stringify(payload));
            editingMsgId = null;
            // Immediately restore UI — server will confirm via message_edited event
            const msgEl = document.getElementById(`msg-${msgId}`);
            if (msgEl) {
                const inp = msgEl.querySelector("input[type=text]");
                if (inp) {
                    const span = document.createElement("span");
                    span.className = "msg-text";
                    span.innerText = newContent.trim();
                    inp.replaceWith(span);
                }
                const btn = msgEl.querySelector(".edit-confirm-btn");
                if (btn) btn.remove();
            }
        }

        function cancelEdit(msgId, original) {
            const msgEl = document.getElementById(`msg-${msgId}`);
            if (!msgEl) return;
            const inp = msgEl.querySelector("input[type=text]");
            if (inp) {
                const span = document.createElement("span");
                span.className = "msg-text";
                span.innerText = original;
                inp.replaceWith(span);
            }
            const confirmBtn = msgEl.querySelector(".edit-confirm-btn");
            if (confirmBtn) confirmBtn.remove();
            editingMsgId = null;
        }

        function handleMessageEdited(event) {
            const msgEl = document.getElementById(`msg-${event.message_id}`);
            if (!msgEl) return;
            let textEl = msgEl.querySelector(".msg-text");
            if (!textEl) {
                textEl = document.createElement("span");
                textEl.className = "msg-text";
                msgEl.appendChild(textEl);
            }
            textEl.innerText = event.new_content;
            let tag = msgEl.querySelector(".edited-tag");
            if (!tag) {
                tag = document.createElement("span");
                tag.className = "edited-tag";
                tag.style.cssText = "font-size:0.75em;color:#94a3b8;margin-left:4px;";
                textEl.after(tag);
            }
            const editedTime = event.edited_at ? new Date(event.edited_at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : "";
            tag.innerText = editedTime ? `(edited ${editedTime})` : "(edited)";
            if (messageMap[event.message_id]) {
                messageMap[event.message_id].content = event.new_content;
                messageMap[event.message_id].edited_at = event.edited_at;
            }
        }

        // --------------- Pin message ---------------
        function pinMsg(msgId) {
            if (!socket || !activeView) return;
            const threadId = (activeView.type === "dm" || activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id;
            socket.send(JSON.stringify({ cmd: "pin_message", message_id: msgId, thread_id: threadId }));
        }

        function showPinPanel() {
            if (!socket || !activeView) return;
            const threadId = (activeView.type === "dm" || activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id;
            socket.send(JSON.stringify({ cmd: "get_pins", thread_id: threadId }));
            document.getElementById("pin-panel").style.display = "block";
        }

        function renderPins(pins) {
            const list = document.getElementById("pin-list");
            list.innerHTML = "";
            if (!pins || pins.length === 0) {
                list.innerHTML = '<p style="color:#94a3b8">No pinned messages.</p>';
                return;
            }
            const threadId = activeView
                ? (activeView.type === "local_dm" ? "dm:" : "room:") + activeView.id
                : "";
            pins.forEach(pin => {
                const div = document.createElement("div");
                div.style.cssText = "border-bottom:1px solid #334155;padding:8px 0;color:#f1f5f9;";
                const pinner = (pin.pinned_by || "").slice(0, 20) || "unknown";
                const preview = (pin.content || "").slice(0, 80);
                div.innerHTML = `<div style="font-size:0.85em;color:#94a3b8">${pinner}</div>
                    <div style="margin:2px 0;">${preview.replace(/</g,"&lt;")}</div>
                    <div style="display:flex;gap:8px;margin-top:4px;">
                        <button data-pin-action="jump" data-msg-id="${pin.message_id}"
                            style="background:transparent;border:none;color:#7dd3fc;cursor:pointer;padding:0;font-size:0.8em;">[Jump]</button>
                        <button data-pin-action="unpin" data-msg-id="${pin.message_id}" data-thread-id="${threadId}"
                            style="background:transparent;border:none;color:#94a3b8;cursor:pointer;padding:0;font-size:0.8em;">Unpin</button>
                    </div>`;
                list.appendChild(div);
            });
        }

        function unpinMsg(msgId, threadId) {
            if (!socket) return;
            socket.send(JSON.stringify({ cmd: "unpin_message", message_id: msgId, thread_id: threadId }));
        }

        function jumpToMsg(msgId) {
            const el = document.getElementById(`msg-${msgId}`);
            if (el) { el.scrollIntoView({ behavior: "smooth" }); el.style.background = "#334155"; setTimeout(() => el.style.background = "", 1500); }
        }

        // --------------- Onboarding ---------------
        function setPodBanner(show) {
            const el = document.getElementById("pod-connect-banner");
            if (el) el.style.display = show ? "flex" : "none";
        }
        function _showNatWarning() {
            if (document.getElementById("nat-warning-banner")) return;
            if (sessionStorage.getItem("proxion_nat_dismissed")) return;
            // Fetch connectivity details to give actionable, user-friendly guidance
            fetch("/connectivity").then(r => r.json()).then(c => {
                if (c.public_url_set) return;
                const banner = document.createElement("div");
                banner.id = "nat-warning-banner";
                banner.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2000;background:#78350f;color:#fef3c7;padding:10px 16px;font-size:0.85em;line-height:1.5;";
                const port = c.local_port || 8080;
                const localIp = c.local_ip || "192.168.x.x";
                const triedUpnp = c.upnp_mapped === false;
                let guide;
                if (triedUpnp) {
                    guide = `<strong>Your gateway isn’t reachable from the internet.</strong>
                        Friends on other gateways can’t message or call you yet.
                        <details style="margin-top:6px;cursor:pointer;">
                          <summary><strong>How to fix this ▾</strong></summary>
                          <div style="margin-top:8px;line-height:1.9;padding:0 4px;">
                            <strong>Option 1 — Port forward your router</strong> (most reliable)<br>
                            Forward port <code style="background:#451a03;padding:1px 4px;border-radius:3px;">${port}</code> (TCP)
                            to <code style="background:#451a03;padding:1px 4px;border-radius:3px;">${localIp}</code> in your router admin page,
                            then set <code style="background:#451a03;padding:1px 4px;border-radius:3px;">PROXION_PUBLIC_URL=http://YOUR_EXTERNAL_IP:${port}</code> in your <code>.env</code>.
                            &nbsp;<a href="https://portforward.com" target="_blank" rel="noopener" style="color:#fcd34d;">portforward.com</a> has guides for every router.<br><br>
                            <strong>Option 2 — Cloudflare Tunnel</strong> (free, no router changes needed)<br>
                            Run: <code style="background:#451a03;padding:1px 4px;border-radius:3px;">cloudflared tunnel --url http://localhost:${port}</code><br>
                            Copy the <code>https://xxxx.trycloudflare.com</code> URL it gives you and set it as <code>PROXION_PUBLIC_URL</code>.
                          </div>
                        </details>`;
                } else {
                    guide = `Your gateway isn’t publicly reachable. Friends on other gateways won’t be able to message or call you. Open Settings → Federation for setup guidance.`;
                }
                banner.innerHTML = `<div style="display:flex;gap:12px;align-items:flex-start;max-width:900px;margin:0 auto;">
                    <span style="flex:1;">${guide}</span>
                    <button style="background:transparent;border:none;color:#fef3c7;cursor:pointer;font-size:1.2em;flex-shrink:0;padding:0 4px;line-height:1;" aria-label="Dismiss">×</button>
                </div>`;
                banner.querySelector("button").onclick = () => {
                    banner.remove();
                    sessionStorage.setItem("proxion_nat_dismissed", "1");
                };
                document.body.prepend(banner);
            }).catch(() => {
                // Fallback: minimal banner if /connectivity unreachable
                const banner = document.createElement("div");
                banner.id = "nat-warning-banner";
                banner.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2000;background:#78350f;color:#fef3c7;padding:8px 16px;font-size:0.85em;display:flex;gap:8px;";
                banner.innerHTML = `<span style="flex:1">Federation limited — gateway not publicly reachable. Set <code>PROXION_PUBLIC_URL</code> in <code>.env</code>.</span><button onclick="this.closest('#nat-warning-banner').remove()" style="background:transparent;border:none;color:#fef3c7;cursor:pointer;">×</button>`;
                document.body.prepend(banner);
            });
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
                const colors = {online:'#4ade80', away:'#fbbf24', busy:'#f87171', offline:'#475569'};
                const st = d.status || 'offline';
                statusEl.innerHTML = `<span style="color:${colors[st]||'#475569'}">&#x25cf;</span> ${st}`;
            }
            const avatarEl = document.getElementById('contact-profile-avatar');
            if (avatarEl) {
                const initials = (d.display_name || d.did || '?').slice(0,2).toUpperCase();
                avatarEl.textContent = initials;
            }
            const dmBtn = document.getElementById('contact-profile-dm-btn');
            if (dmBtn) dmBtn.dataset.webid = d.did || '';
            const didEl = document.getElementById('contact-profile-did');
            if (didEl) {
                didEl.onclick = () => navigator.clipboard.writeText(d.did || '').then(() => showToast('DID copied'));
            }
        }
        function openSettingsToPod() {
            document.getElementById("settings-btn").click();
        }
        function obPodMode(mode) {
            const customInput = document.getElementById("ob-solid-custom-url");
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
                const customInput = document.getElementById("ob-solid-custom-url");
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
            if (socket) socket.send(JSON.stringify({ cmd: "set_identity", display_name: name }));
            podWriteProfile({ displayName: name }).catch(() => {});
            obGoto(3);
        }
        function finishOnboarding() {
            document.getElementById("onboarding-modal").style.display = "none";
            if (socket && socket.readyState === WebSocket.OPEN)
                socket.send(JSON.stringify({ cmd: "set_presence", status: "online" }));
            // R18.1.2: enable autostart the first time setup completes
            if (window.__TAURI__?.invoke && !localStorage.getItem('proxion_wizard_done')) {
                window.__TAURI__.invoke('plugin:autostart|enable').catch(() => {});
            }
            localStorage.setItem('proxion_wizard_done', '1');
        }
        function obSkipPod() {
            const msg = "Without a pod, your messages won't sync across devices and won't be backed up.\n\nAre you sure you want to continue without a pod?";
            if (!window.confirm(msg)) return;
            localStorage.setItem("proxion_pod_setup_skipped", "1");
            localStorage.removeItem("proxion_pod_banner_dismissed");
            setPodBanner(true);
            obGoto(5);
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
                statusEl.textContent = "Could not reach gateway. Is Proxion running?";
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

        // --------------- Keyboard shortcuts ---------------
        function handleShortcut(e) {
            if (e.ctrlKey && e.key === "k") {
                e.preventDefault();
                document.getElementById("search-input").focus();
            } else if (e.ctrlKey && e.key === "/") {
                e.preventDefault();
                const modal = document.getElementById("shortcut-modal");
                modal.style.display = modal.style.display === "flex" ? "none" : "flex";
            } else if (e.key === "Escape") {
                ["settings-modal","shortcut-modal","room-create-modal",
                 "room-members-modal","add-peer-modal","join-room-modal"]
                    .forEach(id => { const el = document.getElementById(id); if (el) el.style.display = "none"; });
                document.getElementById("pin-panel").style.display = "none";
                cancelReply();
                if (editingMsgId) {
                    const msgEl = document.getElementById(`msg-${editingMsgId}`);
                    const inp = msgEl && msgEl.querySelector("input[type=text]");
                    if (inp) cancelEdit(editingMsgId, messageMap[editingMsgId]?.content || "");
                }
            } else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
                e.preventDefault();
                const allItems = Array.from(document.querySelectorAll("nav li"));
                const active = allItems.findIndex(li => li.classList.contains("active"));
                const next = e.key === "ArrowDown"
                    ? Math.min(active + 1, allItems.length - 1)
                    : Math.max(active - 1, 0);
                if (allItems[next]) allItems[next].click();
            }
        }
        document.addEventListener("keydown", handleShortcut);

        // Send voice_hangup when tab/window closes mid-call
        window.addEventListener("beforeunload", () => {
            if (currentCallSessionId && socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({cmd: "voice_hangup", session_id: currentCallSessionId}));
            }
        });

        // Auto-resize textarea
        document.getElementById("message-input").addEventListener("input", function() {
            this.style.height = "auto";
            this.style.height = Math.min(this.scrollHeight, 120) + "px";
        });

        // Send on Enter (not Shift+Enter)
        document.getElementById("message-input").addEventListener("keydown", function(e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                document.getElementById("message-form").dispatchEvent(new Event("submit", { cancelable: true }));
            }
        });

        document.getElementById("message-form").onsubmit = async (e) => {
            e.preventDefault();
            const input = document.getElementById("message-input");
            const content = input.value.trim();
            if (!socket || !activeView) return;

            if (content) {
                let payload;
                const clientMsgId = (typeof crypto !== 'undefined' && crypto.randomUUID)
                    ? crypto.randomUUID()
                    : Math.random().toString(36).slice(2);
                if (activeView.type === "local_dm") {
                    const peerWebid = activeView.peerWebid || activeView.id;
                    let sendContent = content;
                    payload = {
                        cmd: "local_dm",
                        target_webid: peerWebid,
                        content: sendContent,
                        thread_id: activeView.id,
                        message_id: clientMsgId,
                    };
                    // Track new DM threads in pod index so they survive restarts
                    _podUpdateDmIndex(activeView.id, true).catch(() => {});
                    // Attempt E2E encryption if peer pubkey is known
                    if (isE2EEnabled(peerWebid)) {
                        try {
                            const enc = await ratchetEncrypt(peerWebid, content);
                            payload.content     = enc.ciphertext;
                            payload.e2e         = true;
                            payload.nonce       = enc.nonce;
                            payload.msg_num     = enc.msgNum;
                            payload.pn          = enc.pn;
                            payload.ratchet_pub = enc.ratchetPub;
                        } catch (err) {
                            console.warn('[e2e] encrypt failed, sending plaintext:', err);
                        }
                    }
                    // Always announce our X25519 pub key so peer can learn it
                    const myPub = myX25519PubB64u();
                    if (myPub) payload.x25519_pub = myPub;
                } else {
                    const cmd = activeView.type === "dm" ? "send_dm" : "send_room";
                    payload = { cmd: cmd, content: content, message_id: clientMsgId };
                    if (activeView.type === "dm") {
                        payload.cert_id = activeView.id;
                        const peerWebid = activeView.peerWebid;
                        if (peerWebid && isE2EEnabled(peerWebid)) {
                            try {
                                const enc = await ratchetEncrypt(peerWebid, content);
                                payload.content     = enc.ciphertext;
                                payload.e2e         = true;
                                payload.nonce       = enc.nonce;
                                payload.msg_num     = enc.msgNum;
                                payload.pn          = enc.pn;
                                payload.ratchet_pub = enc.ratchetPub;
                            } catch (err) {
                                console.warn('[e2e] encrypt failed for send_dm, sending plaintext:', err);
                            }
                        }
                        const myPub = myX25519PubB64u();
                        if (myPub) payload.x25519_pub = myPub;
                    } else {
                        payload.room_id = activeView.id;
                    }
                }

                if (replyingTo) {
                    payload.reply_to_id = replyingTo.id;
                    cancelReply();
                }

                socketSendOrQueue(payload);

                if (activeView?.type === 'local_room') {
                    podWriteMessageJsonLd(activeView.id, clientMsgId, {
                        content: content,
                        from_webid: selfWebId,
                        from_display_name: localStorage.getItem('proxion_display_name') || '',
                        timestamp: new Date().toISOString(),
                        reply_to_id: replyingTo?.id || null,
                    }, true).catch(err => console.warn('[pod] message write failed:', err));
                    podWriteMessageWithIndex(activeView.id, {
                        message_id: clientMsgId,
                        room_id: activeView.id,
                        from_webid: selfWebId,
                        display_name: localStorage.getItem('proxion_display_name') || '',
                        content: content,
                        timestamp: new Date().toISOString(),
                    }).catch(() => {});
                }

                input.value = "";
                input.style.height = "auto";
            }
        };

        document.getElementById("file-input").onchange = (e) => {
            const file = e.target.files[0];
            if (!file || !socket || !activeView) return;
            if (file.size > 524288) {
                showToast("File too large — max 512 KB");
                e.target.value = "";
                return;
            }
            const reader = new FileReader();
            reader.onload = () => {
                const base64 = reader.result.split(',')[1];
                const fileMsgId = crypto.randomUUID ? crypto.randomUUID() : (Date.now().toString(36));
                const payload = {
                    filename: file.name,
                    mime_type: file.type || "application/octet-stream",
                    data_b64: base64,
                };
                if (activeView.type === "dm" || activeView.type === "local_dm") payload.cert_id = activeView.id;
                else payload.room_id = activeView.id;
                sendCmd("send_file", payload);
                if (activeView.type === 'local_room') {
                    podUploadFile(activeView.id, fileMsgId, file.name, file).catch(() => {});
                }
                e.target.value = "";
            };
            reader.readAsDataURL(file);
        };

        // --------------- System messages ---------------
        function _appendSystemMsg(text) {
            const feed = document.getElementById("message-feed");
            if (!feed) return;
            const el = document.createElement("div");
            el.className = "system-msg";
            el.textContent = text;
            feed.appendChild(el);
            feed.scrollTop = feed.scrollHeight;
        }

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

        // --------------- Copy fallback ---------------
        function showCopyModal(text) {
            const modal = document.getElementById("copy-modal");
            if (!modal) return;
            document.getElementById("copy-modal-text").value = text;
            modal.style.display = "flex";
            setTimeout(() => {
                const ta = document.getElementById("copy-modal-text");
                ta.focus();
                ta.select();
            }, 50);
        }

        // --------------- Add Contact modal ---------------
        const _friendRequestErrors = {
            "invalid_address": "Invalid address — use the format did:key:…@wss://gateway.",
            "delivery_failed": "Could not reach that gateway — check the address and try again.",
            "invalid_signature": "That invite has expired or is invalid.",
            "expired": "That invite has expired.",
            "invite_not_found": "Invite not found — it may have already been used.",
            "contact_revoked": "This contact has been revoked. You can no longer send messages.",
        };

        async function submitAddPeer() {
            let raw = document.getElementById("add-peer-input").value.trim();
            const errEl = document.getElementById("add-peer-error");
            const submitBtn = document.getElementById("add-peer-submit-btn");
            if (!raw) { errEl.textContent = "Please enter an address."; return; }
            if (!socket || socket.readyState !== WebSocket.OPEN) {
                errEl.textContent = "Not connected to gateway."; return;
            }
            // R8.3.3: if value starts with http, extract the ?from= param
            if (raw.startsWith('http')) {
                try {
                    const u = new URL(raw);
                    const extracted = u.searchParams.get('from');
                    if (extracted) raw = extracted;
                } catch (_) {}
            }
            errEl.textContent = "";

            // If address looks like a cross-gateway Proxion address, discover peer first
            if (raw.includes("@") && (raw.includes("http") || raw.startsWith("did:"))) {
                if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "Looking up…"; }
                const discovered = await new Promise(resolve => {
                    _peerDiscoveredResolve = resolve;
                    socket.send(JSON.stringify({ cmd: "discover_peer", address: raw }));
                    setTimeout(() => { _peerDiscoveredResolve = null; resolve(null); }, 8000);
                });
                if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Send Request"; }
                if (!discovered) {
                    errEl.textContent = "Could not reach that gateway — check the address and try again.";
                    return;
                }
                // Show discovered info briefly, then send friend request
                errEl.style.color = "#4ade80";
                errEl.textContent = `Found: ${discovered.display_name || discovered.did.slice(0, 20)} · ${discovered.fingerprint || ""}`;
                setTimeout(() => { errEl.textContent = ""; errEl.style.color = ""; }, 3000);
            }

            if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "Sending…"; }
            _pendingFriendRequest = true;
            if (raw.includes("@")) {
                socket.send(JSON.stringify({cmd: "send_friend_request", target_address: raw}));
            } else {
                socket.send(JSON.stringify({cmd: "resolve_did", did: raw}));
                // resolve_did doesn't use the friend-request flow; close immediately
                _pendingFriendRequest = false;
                document.getElementById("add-peer-modal").style.display = "none";
                if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Send Request"; }
            }
        }
        document.getElementById("add-peer-input").addEventListener("keydown", e => {
            if (e.key === "Enter") submitAddPeer();
        });

        // --------------- Join Room modal ---------------
        function submitJoinRoom() {
            const raw = document.getElementById("join-room-input").value.trim();
            const errEl = document.getElementById("join-room-error");
            if (!socket || socket.readyState !== WebSocket.OPEN) {
                errEl.textContent = "Not connected to gateway."; return;
            }
            if (!raw) { errEl.textContent = "Please enter a link or code."; return; }
            if (raw.startsWith("http://") || raw.startsWith("https://")) {
                try {
                    const url = new URL(raw);
                    const code = url.searchParams.get("join");
                    const sameOrigin = url.origin === window.location.origin;
                    if (sameOrigin && code) {
                        socket.send(JSON.stringify({cmd: "join_room", code: code}));
                    } else if (!sameOrigin) {
                        window.location.href = raw;
                        return;
                    } else {
                        errEl.textContent = "Link has no join code."; return;
                    }
                } catch (e) { errEl.textContent = "Invalid URL: " + e.message; return; }
            } else {
                socket.send(JSON.stringify({cmd: "join_room", code: raw}));
            }
            document.getElementById("join-room-modal").style.display = "none";
        }
        document.getElementById("join-room-input").addEventListener("keydown", e => {
            if (e.key === "Enter") submitJoinRoom();
        });
        document.getElementById("ob-name").addEventListener("keydown", e => {
            if (e.key === "Enter") obStep2();
        });
        document.getElementById("ob-invite-code").addEventListener("keydown", e => {
            if (e.key === "Enter") obStep4Join();
        });

        // --------------- Thread Mute ---------------
        function _saveMuted() {
            localStorage.setItem("proxion_muted_threads", JSON.stringify([...mutedThreads]));
        }
        function muteThread(id) {
            mutedThreads.add(id);
            _saveMuted();
            _rerenderMuteIcon(id);
        }
        function unmuteThread(id) {
            mutedThreads.delete(id);
            _saveMuted();
            _rerenderMuteIcon(id);
        }
        function _rerenderMuteIcon(id) {
            const el = document.getElementById(`nav-${id}`);
            if (!el) return;
            const badge = el.querySelector(".badge");
            if (badge) badge.style.display = mutedThreads.has(id) ? "none" : "";
            const icon = el.querySelector(".mute-icon");
            if (icon) icon.style.display = mutedThreads.has(id) ? "" : "none";
        }

        // --------------- Sidebar Context Menu ---------------
        let _sctxTargetId = null;
        function openSidebarCtx(e, threadId) {
            e.preventDefault();
            e.stopPropagation();
            _sctxTargetId = threadId;
            const isMuted = mutedThreads.has(threadId);
            document.getElementById("sctx-mute").style.display   = isMuted ? "none" : "";
            document.getElementById("sctx-unmute").style.display = isMuted ? "" : "none";
            const menu = document.getElementById("sidebar-ctx-menu");
            menu.style.display = "block";
            const vw = window.innerWidth, vh = window.innerHeight;
            menu.style.left = Math.min(e.clientX, vw - 170) + "px";
            menu.style.top  = Math.min(e.clientY, vh - 110) + "px";
        }
        document.getElementById("sctx-mute").onclick = () => {
            if (_sctxTargetId) muteThread(_sctxTargetId);
            document.getElementById("sidebar-ctx-menu").style.display = "none";
        };
        document.getElementById("sctx-unmute").onclick = () => {
            if (_sctxTargetId) unmuteThread(_sctxTargetId);
            document.getElementById("sidebar-ctx-menu").style.display = "none";
        };
        document.getElementById("sctx-mark-read").onclick = () => {
            if (_sctxTargetId) {
                unreadCounts[_sctxTargetId] = 0;
                updateSidebarBadge(_sctxTargetId);
                _sendUpdateLastRead(_sctxTargetId);
            }
            document.getElementById("sidebar-ctx-menu").style.display = "none";
        };
        document.addEventListener("click", () => {
            document.getElementById("sidebar-ctx-menu").style.display = "none";
        });

        // --------------- @Mention Autocomplete ---------------
        let _mentionStart = -1;
        let _mentionFocusIdx = 0;
        const _msgInput = document.getElementById("message-input");

        _msgInput.addEventListener("input", () => {
            const val = _msgInput.value;
            const caret = _msgInput.selectionStart;
            let atPos = -1;
            for (let i = caret - 1; i >= 0; i--) {
                if (val[i] === "@" && (i === 0 || /\s/.test(val[i - 1]))) { atPos = i; break; }
                if (/\s/.test(val[i])) break;
            }
            if (atPos === -1 || !currentRoomMembers.length) { closeMentionDropdown(); return; }
            const query = val.slice(atPos + 1, caret).toLowerCase();
            const matches = currentRoomMembers.filter(m =>
                (m.display_name || "").toLowerCase().includes(query) ||
                (m.webid || "").toLowerCase().includes(query)
            ).slice(0, 8);
            if (!matches.length) { closeMentionDropdown(); return; }
            _mentionStart = atPos;
            _renderMentionDropdown(matches);
        });

        function _renderMentionDropdown(matches) {
            const dd = document.getElementById("mention-dropdown");
            if (!dd) return;
            _mentionFocusIdx = 0;
            dd.innerHTML = "";
            matches.forEach((m, i) => {
                const name = m.display_name || (m.webid || "").slice(0, 12);
                const color = webidColor(m.webid);
                const initial = (name[0] || "?").toUpperCase();
                const row = document.createElement("div");
                row.className = "mention-option" + (i === 0 ? " focused" : "");
                row.dataset.idx = String(i);
                row.dataset.name = name;

                const avatar = document.createElement("div");
                avatar.className = "mo-avatar";
                avatar.style.background = color;
                avatar.textContent = initial;
                row.appendChild(avatar);

                const label = document.createElement("span");
                label.textContent = name;
                row.appendChild(label);

                if (m.status === "online") {
                    const dot = document.createElement("span");
                    dot.style.width = "6px";
                    dot.style.height = "6px";
                    dot.style.borderRadius = "50%";
                    dot.style.background = "#22c55e";
                    dot.style.display = "inline-block";
                    dot.style.marginLeft = "auto";
                    dot.style.flexShrink = "0";
                    row.appendChild(dot);
                }
                dd.appendChild(row);
            });
            dd.style.display = "block";
        }

        function _selectMention(name) {
            const val = _msgInput.value;
            const caret = _msgInput.selectionStart;
            const before = val.slice(0, _mentionStart);
            const after  = val.slice(caret);
            _msgInput.value = `${before}@${name} ${after}`;
            const newPos = _mentionStart + name.length + 2;
            _msgInput.setSelectionRange(newPos, newPos);
            closeMentionDropdown();
            _msgInput.focus();
        }

        function closeMentionDropdown() {
            const dd = document.getElementById("mention-dropdown");
            if (dd) dd.style.display = "none";
            _mentionStart = -1;
        }

        _msgInput.addEventListener("keydown", e => {
            const dd = document.getElementById("mention-dropdown");
            if (!dd || dd.style.display === "none") return;
            const items = dd.querySelectorAll(".mention-option");
            if (!items.length) return;
            if (e.key === "ArrowDown") {
                e.preventDefault();
                _mentionFocusIdx = Math.min(_mentionFocusIdx + 1, items.length - 1);
                items.forEach((el, i) => el.classList.toggle("focused", i === _mentionFocusIdx));
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                _mentionFocusIdx = Math.max(_mentionFocusIdx - 1, 0);
                items.forEach((el, i) => el.classList.toggle("focused", i === _mentionFocusIdx));
            } else if (e.key === "Enter" || e.key === "Tab") {
                const name = items[_mentionFocusIdx]?.dataset.name;
                if (name) { e.preventDefault(); _selectMention(name); }
            } else if (e.key === "Escape") {
                e.preventDefault(); closeMentionDropdown();
            }
        });

        // --------------- Context Menu ---------------
        let _ctxTarget = null; // { msgId, fromWebid, content, isOwn }

        function openCtxMenu(e, msgId) {
            e.preventDefault();
            e.stopPropagation();
            const msg = messageMap[msgId];
            if (!msg) return;
            _ctxTarget = {
                msgId,
                fromWebid: msg.from_webid,
                content: msg.content || "",
                isOwn: msg.from_webid === selfWebId,
            };
            const isOwn = _ctxTarget.isOwn;
            document.getElementById("ctx-edit").style.display  = isOwn ? "" : "none";
            document.getElementById("ctx-delete").style.display = isOwn ? "" : "none";

            const menu = document.getElementById("ctx-menu");
            menu.style.display = "block";
            const vw = window.innerWidth, vh = window.innerHeight;
            const mw = 200, mh = 180;
            menu.style.left = Math.min(e.clientX, vw - mw) + "px";
            menu.style.top  = Math.min(e.clientY, vh - mh) + "px";
        }

        function closeCtxMenu() {
            document.getElementById("ctx-menu").style.display = "none";
            _ctxTarget = null;
        }

        document.addEventListener("click", closeCtxMenu);
        document.addEventListener("keydown", e => { if (e.key === "Escape") closeCtxMenu(); });

        document.getElementById("ctx-reply").onclick = () => {
            if (!_ctxTarget) return;
            setReply(_ctxTarget.msgId);
            closeCtxMenu();
        };
        document.getElementById("ctx-react").onclick = (e) => {
            e.stopPropagation();
            if (!_ctxTarget) return;
            const msgId = _ctxTarget.msgId;
            const menu = document.getElementById("ctx-menu");
            const rect = menu.getBoundingClientRect();
            closeCtxMenu();
            togglePicker(msgId, rect.left, rect.top);
        };
        document.getElementById("ctx-copy").onclick = () => {
            if (!_ctxTarget) return;
            navigator.clipboard.writeText(_ctxTarget.content).catch(() => {});
            closeCtxMenu();
        };
        document.getElementById("ctx-edit").onclick = () => {
            if (!_ctxTarget) return;
            startEdit(_ctxTarget.msgId);
            closeCtxMenu();
        };
        document.getElementById("ctx-pin").onclick = () => {
            if (!_ctxTarget) return;
            pinMsg(_ctxTarget.msgId);
            closeCtxMenu();
        };
        document.getElementById("ctx-delete").onclick = () => {
            if (!_ctxTarget) return;
            if (!confirm("Delete this message?")) return;
            deleteMsg(_ctxTarget.msgId);
            closeCtxMenu();
        };

        // Long-press for mobile (touch)
        let _longPressTimer = null;
        document.addEventListener("touchstart", e => {
            const msgEl = e.target.closest(".message");
            if (!msgEl) return;
            _longPressTimer = setTimeout(() => {
                const fakeEvt = { clientX: e.touches[0].clientX, clientY: e.touches[0].clientY,
                                  preventDefault: () => {}, stopPropagation: () => {} };
                openCtxMenu(fakeEvt, msgEl.dataset.messageId);
            }, 500);
        }, { passive: true });
        document.addEventListener("touchend", () => {
            clearTimeout(_longPressTimer);
            _longPressTimer = null;
        });

        // Sending update_last_read on thread open and scroll
        function _sendUpdateLastRead(threadId) {
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ cmd: "update_last_read", channel_id: threadId }));
            }
        }

        // --- Solid Pod OIDC integration ---

        // Sync contacts, pending invites, and thread lists from the pod.
        // Called non-blockingly after pod login so the UI isn't held up.
        async function syncFromPod() {
            try {
                // 1. Contacts — rehydrate gateway dm_clients + render sidebar
                const certs = await podReadContacts();
                if (certs.length && socket?.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ cmd: 'restore_contacts', certs }));
                    renderContacts(certs.map(c => {
                        // subject is a raw hex public key — convert to did:key for routing
                        let peerDid = c.peer_did || c.subject || '';
                        if (peerDid && !peerDid.startsWith('did:')) {
                            try {
                                const bytes = new Uint8Array(peerDid.match(/.{2}/g).map(b => parseInt(b, 16)));
                                peerDid = _pubBytesToDid(bytes);
                            } catch (_) { /* keep raw value */ }
                        }
                        return { certificate_id: c.certificate_id, peer_did: peerDid, display_name: c.display_name || null };
                    }));
                }

                // 2. Pending invites
                const invites = await podReadInvites();
                invites.forEach(inv => {
                    if (inv?.invitation_id) renderPendingInvite({
                        invitation_id: inv.invitation_id,
                        from_did: inv.issuer?.did || inv.issuer?.public_key || '',
                        endpoint_hints: inv.endpoint_hints || [],
                    });
                });

                // 3. Room index — populate sidebar for any rooms not already loaded
                const roomIds = await podReadRoomIndex();
                for (const roomId of roomIds) {
                    if (!document.getElementById('room-' + roomId)) {
                        const meta = await podReadRoomMeta(roomId).catch(() => null);
                        if (meta?.room_id) addRoomToSidebar(meta.room_id, meta.name || roomId, meta.invite_url || '');
                    }
                }

                // 4. DM thread index — add any threads not already in sidebar
                const threadIds = await podReadDmIndex();
                for (const tid of threadIds) {
                    if (!localDmPeers[tid]) {
                        localDmPeers[tid] = { display_name: tid.slice(0, 12) + '…', peer_webid: tid };
                    }
                }
                if (threadIds.length) renderDmSidebar();
            } catch (err) {
                console.warn('[syncFromPod] error:', err);
            }
        }

        async function onPodLoggedIn(webId) {
            localStorage.setItem('proxion_pod_webid', webId);
            selfWebId = webId;
            setPodBanner(false);
            await discoverStorageRoot();
            ensureProxionContainer().catch(() => {});
            // Restore display name from pod if missing from localStorage
            const savedName = localStorage.getItem('proxion_display_name');
            if (!savedName) {
                podReadProfile().then(p => {
                    const name = p?.['px:displayName'];
                    if (name) {
                        localStorage.setItem('proxion_display_name', name);
                        if (socket?.readyState === WebSocket.OPEN) {
                            socket.send(JSON.stringify({ cmd: 'set_identity', display_name: name }));
                        }
                    }
                }).catch(() => {});
            }
            const webidEl = document.getElementById('profile-webid');
            if (webidEl) webidEl.textContent = webId;
            const podWebidEl = document.getElementById('settings-pod-webid');
            if (podWebidEl) podWebidEl.textContent = webId; // S5: textContent not innerHTML
            const connDiv = document.getElementById('settings-pod-connected');
            const discDiv = document.getElementById('settings-pod-disconnected');
            if (connDiv) connDiv.style.display = 'block';
            if (discDiv) discDiv.style.display = 'none';
            // Advance onboarding if currently on the pod step
            const podStep = document.getElementById('ob-step-4');
            if (podStep && podStep.style.display !== 'none') obGoto(5);
            if (socket?.readyState === WebSocket.OPEN) {
                const displayName = localStorage.getItem('proxion_display_name') || '';
                socket.send(JSON.stringify({ cmd: 'register', webid: webId, display_name: displayName }));
            }
            // Non-blocking pod sync — contacts, invites, and thread indexes
            syncFromPod().catch(() => {});
        }

        function initPodSettingsPanel() {
            const webId = localStorage.getItem('proxion_pod_webid');
            const connDiv = document.getElementById('settings-pod-connected');
            const discDiv = document.getElementById('settings-pod-disconnected');
            const webidEl = document.getElementById('settings-pod-webid');
            if (webId) {
                if (connDiv) connDiv.style.display = 'block';
                if (discDiv) discDiv.style.display = 'none';
                if (webidEl) webidEl.textContent = webId; // S5: textContent not innerHTML
            } else {
                if (connDiv) connDiv.style.display = 'none';
                if (discDiv) discDiv.style.display = 'block';
            }
        }

        // --- Event listener setup for removed inline handlers ---
        function setupEventListeners() {
            // Helper to safely get element and add listener
            function attachListener(selector, event, handler) {
                const el = document.querySelector(selector);
                if (el) {
                    el.addEventListener(event, handler);
                }
            }

            // Helper to safely attach to multiple elements
            function attachListenersToAll(selector, event, handler) {
                const els = document.querySelectorAll(selector);
                els.forEach(el => {
                    el.addEventListener(event, handler);
                });
            }

            // Emoji picker: all spans with data-emoji attribute
            attachListenersToAll('#emoji-picker [data-emoji]', 'click', function() {
                const emoji = this.getAttribute('data-emoji');
                addEmoji(emoji);
            });

            // Profile card: Send DM button
            attachListener('#profile-dm-btn', 'click', profileCardOpenDM);

            // Chat header: Menu toggle
            attachListener('#menu-toggle', 'click', toggleSidebar);

            // Chat header: Invite button
            attachListener('#invite-btn', 'click', copyRoomInvite);

            // Chat header: Pin panel button
            attachListener('#pin-panel-btn', 'click', showPinPanel);

            // Contact profile panel: Close button
            attachListener('#contact-profile-close', 'click', () => {
                const p = document.getElementById('contact-profile-panel');
                if (p) p.style.display = 'none';
            });

            // Contact profile panel: Send DM button
            attachListener('#contact-profile-dm-btn', 'click', () => {
                const webid = document.getElementById('contact-profile-dm-btn')?.dataset.webid;
                if (webid) {
                    const p = document.getElementById('contact-profile-panel');
                    if (p) p.style.display = 'none';
                    const addPeerInput = document.getElementById('add-peer-input');
                    if (addPeerInput) {
                        addPeerInput.value = webid;
                        document.getElementById('add-peer-btn')?.click();
                    }
                }
            });

            // Message feed: Scroll to bottom button
            attachListener('#scroll-bottom-btn', 'click', scrollToBottom);

            // Reply bar: Cancel reply
            attachListener('#cancel-reply-btn', 'click', cancelReply);

            // Room create modal: Cancel button
            attachListener('#room-create-cancel-btn', 'click', () => {
                document.getElementById('room-create-modal').style.display = 'none';
            });

            // Room create modal: Invite URL (click to copy)
            attachListener('#room-invite-url', 'click', copyRoomInviteFromModal);

            // Room create modal: Done button
            attachListener('#room-create-done-btn', 'click', () => {
                document.getElementById('room-create-modal').style.display = 'none';
            });

            // Settings modal: Cancel button
            attachListener('#settings-cancel-btn', 'click', () => {
                document.getElementById('settings-modal').style.display = 'none';
            });

            // Round 62: Voice recording button
            attachListener('#voice-record-btn', 'mousedown', () => startVoiceRecording());
            attachListener('#voice-record-btn', 'mouseup', () => stopVoiceRecording(true));
            attachListener('#voice-record-btn', 'touchstart', e => { e.preventDefault(); startVoiceRecording(); }, {passive:false});
            attachListener('#voice-record-btn', 'touchend', () => stopVoiceRecording(true));
            attachListener('#voice-record-cancel', 'click', () => stopVoiceRecording(false));

            // Round 65: Disappear timer select
            attachListener('#disappear-timer-select', 'change', e => {
                if (!activeView || !socket) return;
                socket.send(JSON.stringify({cmd:'set_disappear_timer', room_id:activeView.id, ms:parseInt(e.target.value)}));
            });

            // Round 67: Screenshare button
            attachListener('#screenshare-btn', 'click', () => _isSharing ? stopScreenShare() : startScreenShare());

            // Round 68: Forward modal close
            attachListener('#forward-modal-close', 'click', () => {
                document.getElementById('forward-modal').style.display = 'none';
            });

            // Round 69: Schedule button and picker
            attachListener('#schedule-btn', 'click', openSchedulePicker);
            attachListener('#schedule-confirm-btn', 'click', () => {
                const dt = document.getElementById('schedule-datetime-input').value;
                const content = document.getElementById('message-input').value.trim();
                if (!dt || !content || !activeView || !socket) return;
                const sendAt = Math.floor(new Date(dt).getTime() / 1000);
                sendCmd('schedule_message', {thread_id:activeView.id, content, send_at:sendAt});
                document.getElementById('message-input').value = '';
                document.getElementById('schedule-picker').style.display = 'none';
            });
            attachListener('#schedule-cancel-btn', 'click', () => {
                document.getElementById('schedule-picker').style.display = 'none';
            });

            // Round 70: Webhook modal buttons
            attachListener('#webhook-created-close', 'click', () => {
                document.getElementById('webhook-created-modal').style.display = 'none';
            });
            attachListener('#copy-webhook-url-btn', 'click', () => {
                const url = document.getElementById('webhook-url-display').textContent;
                navigator.clipboard.writeText(url).then(() => showToast('Webhook URL copied'));
            });
            attachListener('#integrations-btn', 'click', openIntegrationsPanel);

            // Round 63: Delete submenu buttons
            document.addEventListener('click', e => {
                const sub = document.getElementById('delete-submenu');
                if (sub && !sub.contains(e.target)) sub.style.display = 'none';
                if (e.target.id === 'delete-for-me-btn') {
                    deleteMsg(e.target.dataset.msgId);
                    sub.style.display = 'none';
                } else if (e.target.id === 'delete-for-everyone-btn') {
                    const mid = e.target.dataset.msgId;
                    if (socket && mid) socket.send(JSON.stringify({cmd:'delete_message', message_id:mid, for_everyone:true}));
                    document.getElementById(`msg-${mid}`)?.remove();
                    sub.style.display = 'none';
                }
            });

            // Shortcuts modal: Close button
            attachListener('#shortcut-close-btn', 'click', () => {
                document.getElementById('shortcut-modal').style.display = 'none';
            });

            // Onboarding: Step 1 - Get Started
            attachListener('#ob-start-btn', 'click', () => obGoto(2));

            // Onboarding: Step 2 - Continue
            attachListener('#ob-step2-btn', 'click', obStep2);

            // Onboarding: Step 3 - Continue
            attachListener('#ob-step3-btn', 'click', obStep3);

            // Onboarding: Step 4 - Create a Room
            attachListener('#ob-step5-create', 'click', obStep4Create);

            // Onboarding: Step 5 - Join with Code
            attachListener('#ob-step5-join', 'click', obStep4Join);

            // Onboarding: Step 6 - Finish (Open Proxion)
            attachListener('#ob-finish-btn', 'click', () => {
                document.getElementById('onboarding-modal').style.display = 'none';
                // Auto-select the first room so the user lands in a channel, not a blank view
                setTimeout(() => {
                    const firstRoom = document.querySelector('#room-list li');
                    if (firstRoom) firstRoom.click();
                }, 100);
            });
            attachListener('#ob-copy-invite-btn', 'click', copyObInviteUrl);

            // Pin panel: Close button
            attachListener('#pin-panel-close', 'click', () => {
                document.getElementById('pin-panel').style.display = 'none';
            });

            // Chat header: Members toggle, Delete room, Leave room
            attachListener('#members-toggle', 'click', toggleMembersPanel);
            attachListener('#delete-room-btn', 'click', deleteRoom);
            attachListener('#leave-room-btn', 'click', leaveRoom);
            attachListener('#leave-voice-channel-btn', 'click', leaveVoiceChannel);

            // Members panel close
            attachListener('#members-panel-close', 'click', toggleMembersPanel);

            // Username click reconnect
            attachListener('#username', 'click', forceReconnect);

            // Address copy
            attachListener('#my-address-short', 'click', copyMyAddress);
            attachListener('#copy-addr-btn', 'click', copyMyAddress);
            attachListener('#share-invite-btn', 'click', shareInviteLink);

            // R17.1: QR share panel buttons
            attachListener('#qr-close-btn', 'click', () => {
                document.getElementById('qr-share-panel').style.display = 'none';
            });
            attachListener('#qr-download-btn', 'click', () => {
                const canvas = document.querySelector('#my-qr canvas');
                if (!canvas) return;
                const a = document.createElement('a');
                a.href = canvas.toDataURL('image/png');
                a.download = 'proxion-invite-qr.png';
                a.click();
            });
            attachListener('#qr-copy-invite-btn', 'click', () => {
                const link = window.proxionInviteLink || '';
                if (!link) return;
                navigator.clipboard.writeText(link).then(() => showToast('Invite link copied!'));
            });
            attachListener('#qr-copy-short-btn', 'click', () => {
                const link = window.proxionShortInviteUrl || window.proxionInviteLink || '';
                if (!link) return;
                navigator.clipboard.writeText(link).then(() => showToast('Short link copied!'));
            });

            // R17.2: QR scan — decode image and pre-fill add-peer input
            const scanInput = document.getElementById('scan-qr-input');
            if (scanInput) {
                scanInput.addEventListener('change', async (e) => {
                    const file = e.target.files[0];
                    const errEl = document.getElementById('scan-qr-error');
                    if (!file) return;
                    const bitmap = await createImageBitmap(file);
                    const canvas = document.createElement('canvas');
                    canvas.width = bitmap.width;
                    canvas.height = bitmap.height;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(bitmap, 0, 0);
                    const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                    const result = typeof jsQR !== 'undefined' ? jsQR(imgData.data, imgData.width, imgData.height) : null;
                    scanInput.value = '';
                    if (!result) {
                        if (errEl) { errEl.textContent = 'No QR code found.'; errEl.style.display = 'inline'; }
                        return;
                    }
                    if (errEl) errEl.style.display = 'none';
                    // extract proxion_address from invite URL param "from"
                    let address = result.data;
                    try {
                        const u = new URL(result.data);
                        const from = u.searchParams.get('from');
                        if (from) address = decodeURIComponent(from);
                    } catch (_) {}
                    const peerInput = document.getElementById('peer-address');
                    if (peerInput) {
                        peerInput.value = address;
                        peerInput.focus();
                    }
                });
            }

            // Pod connect banner
            attachListener('#pod-banner-text', 'click', openSettingsToPod);
            attachListener('#pod-banner-connect-btn', 'click', openSettingsToPod);
            attachListener('#pod-banner-dismiss-btn', 'click', (e) => {
                e.stopPropagation();
                setPodBanner(false);
                localStorage.setItem("proxion_pod_banner_dismissed", "1");
            });

            // Room invite URL click to copy
            attachListener('#room-invite-url', 'click', copyRoomInviteFromModal);

            // Room members modal close
            attachListener('#room-members-close-btn', 'click', () => {
                document.getElementById('room-members-modal').style.display = 'none';
            });

            // R32: Room bans panel
            attachListener('#room-bans-close', 'click', () => {
                document.getElementById('room-bans-panel').style.display = 'none';
            });

            // Onboarding: skip pod
            attachListener('#ob-skip-pod', 'click', (e) => { e.preventDefault(); obSkipPod(); });
            attachListener('#ob-finish-skip', 'click', finishOnboarding);

            // R16: provider card selection
            document.querySelectorAll('.ob-provider-card').forEach(btn => {
                btn.addEventListener('click', () => obSelectProvider(btn.dataset.css));
            });
            attachListener('#ob-pod-back-btn', 'click', () => {
                document.getElementById('ob-pod-providers').style.display = 'flex';
                document.getElementById('ob-pod-cred-form').style.display = 'none';
            });
            attachListener('#ob-pod-test-btn', 'click', obPodTestConnection);
            attachListener('#ob-pod-continue-btn', 'click', () => {
                if (!document.getElementById('ob-pod-continue-btn').disabled) obGoto(5);
            });
            attachListener('#ob-pod-pw-toggle', 'click', () => {
                const pw = document.getElementById('ob-pod-password');
                const btn = document.getElementById('ob-pod-pw-toggle');
                if (pw.type === 'password') { pw.type = 'text'; btn.textContent = 'Hide'; }
                else { pw.type = 'password'; btn.textContent = 'Show'; }
            });

            // Settings: OIDC sign-in buttons
            attachListener('#settings-solid-solidcommunity', 'click', () => solidLogin('https://solidcommunity.net'));
            attachListener('#settings-solid-inrupt', 'click', () => solidLogin('https://inrupt.net'));
            attachListener('#settings-solid-custom-btn', 'click', () => {
                const url = (document.getElementById('settings-solid-custom-url')?.value || '').trim();
                if (!url.startsWith('https://')) { alert('Pod server URL must start with https://'); return; }
                solidLogin(url);
            });
            // Disconnect from the CSS/Solid pod without touching the local identity key or
            // room/message history. The user's DID, display name, and all rooms are preserved.
            async function _signOutOfPod() {
                if (window.__TAURI__?.invoke) {
                    window.__TAURI__.invoke('clear_pod_credentials').catch(() => {});
                }
                // Tell gateway to drop pod credentials (HTTP endpoint — no WS auth needed)
                try { await fetch('/api/pod-disconnect', { method: 'POST' }); } catch (_) {
                    if (socket && socket.readyState === WebSocket.OPEN)
                        socket.send(JSON.stringify({ cmd: 'disconnect_pod' }));
                }
                // Clear Solid OIDC browser session (if any) with timeout
                try {
                    await Promise.race([
                        solidSession.logout({ logoutType: 'app' }),
                        new Promise(r => setTimeout(r, 1500)),
                    ]);
                } catch (_) {}
                // Clear only pod-related localStorage — identity, rooms, and messages survive
                const _podKeys = [
                    'proxion_pod_connected', 'proxion_pod_webid', 'proxion_css_url',
                    'proxion_pod_setup_skipped', 'proxion_pod_banner_dismissed',
                ];
                _podKeys.forEach(k => localStorage.removeItem(k));
                window.location.reload();
            }

            // Full wipe: clears identity key, all data, and pod credentials.
            // Only used by "Reset Identity" — not by normal pod sign-out.
            async function _resetIdentity() {
                if (window.__TAURI__?.invoke) {
                    window.__TAURI__.invoke('clear_pod_credentials').catch(() => {});
                }
                try { await fetch('/api/pod-disconnect', { method: 'POST' }); } catch (_) {}
                try {
                    await Promise.race([
                        solidSession.logout({ logoutType: 'app' }),
                        new Promise(r => setTimeout(r, 1500)),
                    ]);
                } catch (_) {}
                localStorage.clear();
                sessionStorage.clear();
                try {
                    const dbs = await (indexedDB.databases?.() ?? Promise.resolve([]));
                    await Promise.allSettled(dbs.map(({name}) => new Promise((res, rej) => {
                        const r = indexedDB.deleteDatabase(name);
                        r.onsuccess = res; r.onerror = rej; r.onblocked = res;
                    })));
                } catch (_) {}
                window.location.reload();
            }
            attachListener('#settings-pod-logout-btn', 'click', _signOutOfPod);
            // R18.1.3: autostart toggle
            attachListener('#settings-autostart-toggle', 'change', (e) => {
                if (!window.__TAURI__?.invoke) return;
                const cmd = e.target.checked ? 'plugin:autostart|enable' : 'plugin:autostart|disable';
                window.__TAURI__.invoke(cmd).catch(() => {});
            });
            // R18.3.3: about website
            attachListener('#settings-about-website-btn', 'click', () => {
                const url = 'https://github.com/proxion-messenger';
                if (window.__TAURI__?.shell?.open) {
                    window.__TAURI__.shell.open(url);
                } else {
                    window.open(url, '_blank', 'noopener');
                }
            });
            attachListener('#settings-reset-identity-btn', 'click', async () => {
                if (!confirm('This will permanently delete your local identity key, all rooms, messages, and pod credentials. You cannot undo this. Continue?')) return;
                await _resetIdentity();
            });

            // R14.3: Export/Import
            const exportBtn = document.getElementById('export-data-btn');
            if (exportBtn) exportBtn.onclick = () => {
                const a = document.createElement('a');
                a.href = '/export';
                a.download = `proxion-export-${new Date().toISOString().slice(0,10)}.json`;
                a.click();
            };
            const importInput = document.getElementById('import-file-input');
            if (importInput) importInput.onchange = async (e) => {
                const file = e.target.files[0];
                if (!file) return;
                showToast('Importing…');
                const text = await file.text();
                const resp = await fetch('/import', { method: 'POST', body: text, headers: {'Content-Type':'application/json'} });
                const result = await resp.json();
                if (result.status === 'ok') showToast(`Import complete: ${result.counts?.messages || 0} messages`);
                else showToast('Import failed: ' + (result.error || 'unknown error'));
                importInput.value = '';
            };

            initPodSettingsPanel();

            // R10.3.1: Read receipt toggle
            const receiptsToggle = document.getElementById('settings-receipts-toggle');
            if (receiptsToggle) {
                receiptsToggle.checked = localStorage.getItem("proxion_receipts_enabled") !== "0";
                receiptsToggle.onchange = () => {
                    const enabled = receiptsToggle.checked;
                    localStorage.setItem("proxion_receipts_enabled", enabled ? "1" : "0");
                    if (socket?.readyState === WebSocket.OPEN)
                        socket.send(JSON.stringify({cmd: "set_receipts_enabled", enabled}));
                };
            }

            // R10.3.2: Link preview toggle (default off)
            const previewsToggle = document.getElementById('settings-link-previews-toggle');
            if (previewsToggle) {
                previewsToggle.checked = localStorage.getItem("proxion_link_previews_enabled") === "1";
                previewsToggle.onchange = () => {
                    const enabled = previewsToggle.checked;
                    localStorage.setItem("proxion_link_previews_enabled", enabled ? "1" : "0");
                    if (socket?.readyState === WebSocket.OPEN)
                        socket.send(JSON.stringify({cmd: "set_link_previews_enabled", enabled}));
                };
            }

            // R10.6.1: Deep link confirmation modal buttons
            document.getElementById('deeplink-confirm-cancel')?.addEventListener('click', () => {
                document.getElementById('deeplink-confirm-modal').style.display = 'none';
            });
            document.getElementById('deeplink-confirm-ok')?.addEventListener('click', () => {
                const modal = document.getElementById('deeplink-confirm-modal');
                const address = modal.dataset.pendingAddress || '';
                modal.style.display = 'none';
                const addModal = document.getElementById('add-peer-modal');
                const input = document.getElementById('peer-address');
                if (addModal && input && address) {
                    input.value = address;
                    addModal.style.display = 'flex';
                    input.focus();
                }
            });

            // Empty state quick-action buttons
            attachListener('#empty-create-room-btn', 'click', () => document.getElementById('create-room-btn').click());
            attachListener('#empty-add-contact-btn', 'click', () => document.getElementById('add-peer-btn').click());

            // Event delegation: #messages — message action buttons
            document.getElementById('message-feed')?.addEventListener('click', e => {
                const el = e.target.closest('[data-msg-action]');
                if (!el) return;
                e.stopPropagation();
                const { msgAction, msgId, webid, name, replyId } = el.dataset;
                switch (msgAction) {
                    case 'edit':         startEdit(msgId); break;
                    case 'delete': {
                        const fromWebid = el.dataset.fromWebid || '';
                        const isSender = fromWebid === selfWebId;
                        const sub = document.getElementById('delete-submenu');
                        if (sub) {
                            sub.querySelector('#delete-for-me-btn').dataset.msgId = msgId;
                            sub.querySelector('#delete-for-everyone-btn').dataset.msgId = msgId;
                            sub.querySelector('#delete-for-everyone-btn').style.display = isSender ? '' : 'none';
                            sub.style.cssText = `display:block;top:${e.clientY}px;left:${e.clientX}px;`;
                        } else { deleteMsg(msgId); }
                        break;
                    }
                    case 'forward':      openForwardModal(msgId); break;
                    case 'react':        togglePicker(msgId, e.clientX, e.clientY); break;
                    case 'reply':        setReply(msgId); break;
                    case 'pin':          pinMsg(msgId); break;
                    case 'profile':
                        showProfileCard(webid, name, e.clientX, e.clientY);
                        showContactProfile(webid);
                        break;
                    case 'scroll-reply': document.getElementById(`msg-${replyId}`)?.scrollIntoView({ behavior: 'smooth' }); break;
                }
            });

            // Event delegation: #friend-request-list
            document.getElementById('friend-request-list')?.addEventListener('click', e => {
                const btn = e.target.closest('[data-fr-action]');
                if (!btn) return;
                const { frAction, invId } = btn.dataset;
                if (frAction === 'accept') acceptFriendRequest(invId);
                else if (frAction === 'dismiss') {
                    document.getElementById(`fri-${invId}`)?.remove();
                    refreshFriendRequestsBadge();
                }
            });

            // Event delegation: #room-members-list — kick/transfer/ban/mute
            document.getElementById('room-members-list')?.addEventListener('click', e => {
                const btn = e.target.closest('[data-rm-action]');
                if (btn) {
                    const { rmAction, roomId, webid } = btn.dataset;
                    if (rmAction === 'kick') kickMember(roomId, webid);
                    else if (rmAction === 'transfer') transferOwnership(roomId, webid);
                    else if (rmAction === 'ban') {
                        showConfirm(`Ban this member?`, () => {
                            const reason = prompt("Reason (optional):") || "";
                            socket.send(JSON.stringify({cmd: "ban_member", room_id: roomId, webid, reason}));
                        });
                    } else if (rmAction === 'mute') {
                        const dur = prompt("Mute duration: 5m / 1h / 24h / blank=indefinite") || "";
                        const secs = dur === "5m" ? 300 : dur === "1h" ? 3600 : dur === "24h" ? 86400 : null;
                        socket.send(JSON.stringify({
                            cmd: "mute_member", room_id: roomId, webid,
                            ...(secs !== null ? {duration_seconds: secs} : {}),
                        }));
                    }
                    return;
                }
                const item = e.target.closest('[data-msg-action="profile"]');
                if (item) {
                    showProfileCard(item.dataset.webid, item.dataset.name, e.clientX, e.clientY);
                    showContactProfile(item.dataset.webid);
                }
            });

            // Event delegation: #contacts-list — member-item profile cards
            document.getElementById('contacts-list')?.addEventListener('click', e => {
                const item = e.target.closest('[data-msg-action="profile"]');
                if (item) {
                    showProfileCard(item.dataset.webid, item.dataset.name, e.clientX, e.clientY);
                    showContactProfile(item.dataset.webid);
                }
            });

            // Round 64: member-context-menu — right-click on member items to assign roles
            document.getElementById('members-list')?.addEventListener('contextmenu', e => {
                const item = e.target.closest('.member-item');
                if (!item) return;
                e.preventDefault();
                const menu = document.getElementById('member-context-menu');
                if (!menu) return;
                menu.dataset.targetWebid = item.dataset.webid || '';
                menu.style.display = 'block';
                menu.style.left = e.clientX + 'px';
                menu.style.top = e.clientY + 'px';
            });

            document.getElementById('member-context-menu')?.addEventListener('click', e => {
                const btn = e.target.closest('[data-role-action]');
                if (!btn) return;
                const menu = document.getElementById('member-context-menu');
                const targetWebid = menu?.dataset.targetWebid;
                if (!targetWebid || !activeView || !socket) { menu.style.display = 'none'; return; }
                const action = btn.dataset.roleAction;
                if (action === 'kick') {
                    kickMember(activeView.id, targetWebid);
                } else {
                    socket.send(JSON.stringify({cmd: 'set_member_role', room_id: activeView.id, webid: targetWebid, role: action}));
                }
                menu.style.display = 'none';
            });

            document.addEventListener('click', e => {
                if (!e.target.closest('#member-context-menu')) {
                    document.getElementById('member-context-menu')?.style && (document.getElementById('member-context-menu').style.display = 'none');
                }
            }, true);

            // Event delegation: #room-list — sidebar members button
            document.getElementById('room-list')?.addEventListener('click', e => {
                const btn = e.target.closest('[data-sidebar-action="members"]');
                if (btn) { e.stopPropagation(); showRoomMembers(btn.dataset.roomId); }
            });

            // Event delegation: #pin-panel — jump/unpin buttons
            document.getElementById('pin-panel')?.addEventListener('click', e => {
                const btn = e.target.closest('[data-pin-action]');
                if (!btn) return;
                if (btn.dataset.pinAction === 'jump')  jumpToMsg(btn.dataset.msgId);
                if (btn.dataset.pinAction === 'unpin') unpinMsg(btn.dataset.msgId, btn.dataset.threadId);
            });

            // Event delegation: #mention-dropdown — mention selection
            document.getElementById('mention-dropdown')?.addEventListener('click', e => {
                const item = e.target.closest('[data-name]');
                if (item) _selectMention(item.dataset.name);
            });
        }

        // R17.4.3: handle proxion:// deep link URL
        function _handleDeepLinkUrl(url) {
            if (!url) return;
            try {
                const u = new URL(url);
                const from = u.searchParams.get('from');
                if (from) {
                    const address = decodeURIComponent(from);
                    // R10.6.1: show confirmation modal before opening Add Contact
                    const confirmModal = document.getElementById('deeplink-confirm-modal');
                    const addrEl = document.getElementById('deeplink-confirm-address');
                    if (confirmModal && addrEl) {
                        const truncated = address.length > 60 ? address.slice(0, 60) + '…' : address;
                        addrEl.textContent = truncated;
                        confirmModal.dataset.pendingAddress = address;
                        confirmModal.style.display = 'flex';
                    } else {
                        // Fallback if modal not in DOM
                        const modal = document.getElementById('add-peer-modal');
                        const input = document.getElementById('peer-address');
                        if (modal && input) { input.value = address; modal.style.display = 'flex'; input.focus(); }
                    }
                }
            } catch (_) {}
        }

        // R18.2.2: navigate to a thread from tray unread click
        function _navigateToThread(threadId) {
            if (!threadId) return;
            const li = document.getElementById(`nav-${CSS.escape(threadId)}`);
            if (li) li.click();
        }

        showEmptyState();
        setupEventListeners();
        if (window.__TAURI__?.event?.listen) {
            window.__TAURI__.event.listen("gateway-crashed", () => {
                showToast("Gateway crashed - please restart the app", "error");
            });
            // R17.4.3: deep link from OS protocol handler (cold-start)
            window.__TAURI__.event.listen("deep-link-invoke", ({ payload }) => {
                _handleDeepLinkUrl(payload);
            });
            // R18.2.2: tray unread item click navigates to thread
            window.__TAURI__.event.listen("navigate-to-thread", ({ payload }) => {
                _navigateToThread(payload);
            });
            // R17.4.4: fallback — consume pending link in case event fired before listeners were ready
            window.__TAURI__.event.listen("gateway-ready", () => {
                window.__TAURI__.invoke('consume_pending_deep_link').then(url => {
                    if (url) _handleDeepLinkUrl(url);
                }).catch(() => {});
            });
        }
        // R18.2.3: first-autostart notification
        if (window.__TAURI__?.invoke) {
            window.__TAURI__.invoke('is_autostart_launch').then(isAutostart => {
                if (isAutostart && !localStorage.getItem('proxion_autostart_notified')) {
                    localStorage.setItem('proxion_autostart_notified', '1');
                    sendNotification('Proxion is running', 'Click the tray icon to open it.');
                }
            }).catch(() => {});
        }
        (async () => {
            const podWebId = await initSolidAuth();
            if (podWebId) await onPodLoggedIn(podWebId);
            else initPodSettingsPanel(); // re-sync now that auth state is known
            await generateOrLoadIdentity();
            if (!podWebId) selfWebId = clientDid;
            await initE2E().catch(() => {});

            // R11.2.2: Safety number bar "Mark as verified" button
            document.getElementById('fingerprint-verify-btn')?.addEventListener('click', () => {
                if (_fingerprintBarDid) {
                    localStorage.setItem("proxion_verified_" + _fingerprintBarDid, "1");
                    _updateIdentityFingerprint(_fingerprintBarDid);
                }
            });

            // R11.3.2: Logout all other devices button
            document.getElementById('settings-logout-all-btn')?.addEventListener('click', () => {
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({cmd: "logout_all_devices"}));
                }
            });

            // R12.1.3: Download identity backup
            document.getElementById('settings-backup-btn')?.addEventListener('click', async () => {
                const pp = prompt('Enter a passphrase to protect your backup:');
                if (!pp) return;
                try {
                    const apiToken = document.querySelector('meta[name="x-api-token"]')?.content || '';
                    const headers = {};
                    if (apiToken) headers['Authorization'] = 'Bearer ' + apiToken;
                    const resp = await fetch('/backup?passphrase=' + encodeURIComponent(pp), { headers });
                    if (!resp.ok) { showToast('Backup failed: ' + resp.status); return; }
                    const blob = await resp.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url; a.download = 'proxion-backup.json'; a.click();
                    URL.revokeObjectURL(url);
                    localStorage.setItem('proxion_backup_downloaded', Date.now().toString());
                    showToast('Identity backup downloaded.');
                } catch (e) { showToast('Backup error: ' + e.message); }
            });

            // R12.1.3: Restore from backup
            document.getElementById('settings-restore-btn')?.addEventListener('click', () => {
                document.getElementById('settings-restore-input')?.click();
            });
            document.getElementById('settings-restore-input')?.addEventListener('change', async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                const pp = prompt('Enter the passphrase for this backup:');
                if (!pp) return;
                try {
                    const data = await file.arrayBuffer();
                    const apiToken = document.querySelector('meta[name="x-api-token"]')?.content || '';
                    const headers = { 'Content-Type': 'application/json' };
                    if (apiToken) headers['Authorization'] = 'Bearer ' + apiToken;
                    const resp = await fetch('/restore?passphrase=' + encodeURIComponent(pp), {
                        method: 'POST', headers, body: data,
                    });
                    if (!resp.ok) { showToast('Restore failed: ' + resp.status); return; }
                    showToast('Identity restored. Reconnecting…');
                    setTimeout(() => { if (socket) socket.close(); }, 1000);
                } catch (e) { showToast('Restore error: ' + e.message); }
                e.target.value = '';
            });

            // Wire up E2E verify modal buttons
            document.getElementById('dm-e2e-verify-btn')?.addEventListener('click', () => {
                if (activeView?.peerWebid) _openVerifyModal(activeView.peerWebid);
            });
            document.getElementById('e2e-modal-cancel')?.addEventListener('click', () => {
                document.getElementById('e2e-verify-modal').style.display = 'none';
            });
            document.getElementById('e2e-modal-verify')?.addEventListener('click', () => {
                const peerId = document.getElementById('e2e-modal-current-peer')?.value;
                if (peerId) {
                    localStorage.setItem('proxion_e2e_verified_' + peerId, '1');
                    _updateE2EStatus(peerId);
                }
                document.getElementById('e2e-verify-modal').style.display = 'none';
            });

            // R13.6: First-run backup nudge
            (function _checkBackupNudge() {
                const firstSeen = localStorage.getItem("proxion_first_seen");
                if (!firstSeen) localStorage.setItem("proxion_first_seen", Date.now().toString());
                function _showNudgeIfNeeded() {
                    const fs = parseInt(localStorage.getItem("proxion_first_seen") || "0", 10);
                    const downloaded = localStorage.getItem("proxion_backup_downloaded");
                    const dismissed = localStorage.getItem("proxion_backup_nudge_dismissed");
                    if (!downloaded && !dismissed && Date.now() - fs > 24 * 60 * 60 * 1000) {
                        const nudge = document.getElementById("backup-nudge");
                        if (nudge) nudge.classList.add("visible");
                    }
                }
                setTimeout(_showNudgeIfNeeded, 5000);
                document.getElementById("backup-nudge-action")?.addEventListener("click", () => {
                    document.getElementById("backup-nudge")?.classList.remove("visible");
                    document.getElementById("settings-btn")?.click();
                });
                document.getElementById("backup-nudge-dismiss")?.addEventListener("click", () => {
                    localStorage.setItem("proxion_backup_nudge_dismissed", "1");
                    document.getElementById("backup-nudge")?.classList.remove("visible");
                });
            })();

            // R13.7: Image lightbox
            document.getElementById("lightbox-close")?.addEventListener("click", () => {
                document.getElementById("lightbox")?.classList.remove("visible");
            });
            document.getElementById("lightbox")?.addEventListener("click", (e) => {
                if (e.target === document.getElementById("lightbox"))
                    document.getElementById("lightbox").classList.remove("visible");
            });
            document.addEventListener("keydown", (e) => {
                if (e.key === "Escape") document.getElementById("lightbox")?.classList.remove("visible");
            });
            document.addEventListener("click", (e) => {
                const img = e.target.closest(".msg-image-preview");
                if (img) {
                    const lb = document.getElementById("lightbox");
                    const lbImg = document.getElementById("lightbox-img");
                    if (lb && lbImg) { lbImg.src = img.src; lb.classList.add("visible"); }
                }
            });

            // R13.11: Edit history popover (click on .edited-badge)
            document.addEventListener("click", (e) => {
                const badge = e.target.closest(".edited-badge");
                if (badge) {
                    e.stopPropagation();
                    document.querySelectorAll(".edit-history-popover").forEach(p => p.remove());
                    const msgId = badge.dataset.msgId;
                    if (!msgId) return;
                    const popover = document.createElement("div");
                    popover.className = "edit-history-popover";
                    popover.innerHTML = "<em>Loading…</em>";
                    badge.style.position = "relative";
                    badge.appendChild(popover);
                    fetch(`/message-edits?message_id=${encodeURIComponent(msgId)}`)
                        .then(r => r.json())
                        .then(edits => {
                            if (!edits.length) { popover.innerHTML = "<em>No history available.</em>"; return; }
                            popover.innerHTML = edits.map(ed =>
                                `<div class="edit-history-entry">
                                  <div class="edit-history-meta">${escHtml(new Date(ed.edited_at).toLocaleString())} — ${escHtml(ed.edited_by)}</div>
                                  <div>${escHtml(ed.prev_content)}</div>
                                </div>`
                            ).join("");
                        })
                        .catch(() => { popover.innerHTML = "<em>Could not load history.</em>"; });
                    return;
                }
                // Close popover on outside click
                if (!e.target.closest(".edit-history-popover")) {
                    document.querySelectorAll(".edit-history-popover").forEach(p => p.remove());
                }
            });

            connect();
        })();
