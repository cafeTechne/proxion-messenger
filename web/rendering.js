// Message rendering — the core message feed renderer. Builds message elements,
// threads replies under parents, draws date dividers, manages the
// scroll-to-bottom button and the virtual-scroll "load older on scroll-to-top"
// behavior. This is core slice 2: it is called by the WS dispatch and by
// view-switching, but itself calls relatively few things back.
//
// Host state it READS is taken via getters resolved at the top of each function
// (the functions are synchronous, so a fresh getter call per invocation is safe
// even though view-switching reassigns activeView/messageMap/allMessages between
// calls). messageMap/allMessages are mutated in place here, never reassigned.
// Cluster-owned mutable state (date-divider cursor, scroll-unread counter, the
// older-history in-flight flag) lives in `state`; main.js view-switchers reset
// state._lastRenderedDate directly.
//
// createRendering({
//   getActiveView, getSocket, getSelfWebId, getSelfPubHex,
//   getCurrentDisappearMs, getMessageMap, getAllMessages, getUserPresence,
//   renderReactions, openCtxMenu, sendUpdateLastRead,
//   renderWindow, scrollBatch,
// })

import { didSuffix, escHtml, webidColor, renderMarkdown, timeAgo, expireLabel as _expireLabel } from './util.js';
import { t, getLocale } from './i18n.js';

export function createRendering({
    getActiveView, getSocket, getSelfWebId, getSelfPubHex,
    getCurrentDisappearMs, getMessageMap, getAllMessages, getUserPresence,
    renderReactions, openCtxMenu, sendUpdateLastRead,
    getRoomCode, renderWindow, scrollBatch,
}) {
    const RENDER_WINDOW = renderWindow;
    const SCROLL_BATCH = scrollBatch;
    const state = {
        _lastRenderedDate: null,    // for date dividers (reset by view-switching)
        _scrollBottomUnread: 0,     // count of messages arrived while scrolled up
        _loadingOlderHistory: false,
    };

    function scrollToBottom() {
        const activeView = getActiveView();
        const feed = document.getElementById("message-feed");
        feed.scrollTop = feed.scrollHeight;
        state._scrollBottomUnread = 0;
        document.getElementById("scroll-bottom-btn").style.display = "none";
        if (activeView) sendUpdateLastRead(activeView.id);
    }

    function _dateLabelForTimestamp(ts) {
        const d = new Date(ts);
        const today = new Date();
        const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
        if (d.toDateString() === today.toDateString()) return t('time.today');
        if (d.toDateString() === yesterday.toDateString()) return t('time.yesterday');
        return d.toLocaleDateString(getLocale(), {month:"long", day:"numeric"});
    }

    function renderMessages() {
        const allMessages = getAllMessages();
        const feed = document.getElementById("message-feed");
        const slice = allMessages.slice(-RENDER_WINDOW);
        feed.innerHTML = "";
        state._lastRenderedDate = null;
        _renderThreaded(slice, feed);
        feed.scrollTop = feed.scrollHeight;
    }

    function renderMessage(msg) {
        const activeView = getActiveView();
        const allMessages = getAllMessages();
        const messageMap = getMessageMap();
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
            // The "No messages yet." hero is added when a thread opens empty;
            // clear it the moment real content arrives, or it floats above the
            // messages forever (same stale-empty-state class as the sidebar CTA).
            feed.querySelector(".empty-state")?.remove();
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
                state._scrollBottomUnread++;
                const btn = document.getElementById("scroll-bottom-btn");
                const cnt = document.getElementById("scroll-bottom-count");
                cnt.textContent = state._scrollBottomUnread > 0 ? state._scrollBottomUnread : "";
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
        const messageMap = getMessageMap();
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
        const messageMap = getMessageMap();
        const currentDisappearMs = getCurrentDisappearMs();
        const userPresence = getUserPresence();
        const selfWebId = getSelfWebId();
        const selfPubHex = getSelfPubHex();
        const activeView = getActiveView();
        const socket = getSocket();
        const existing = document.getElementById(`msg-${msg.message_id}`);
        if (existing) return; // already in DOM

        const msgId = msg.message_id;
        messageMap[msgId] = msg;
        const depth = msg._threadDepth || 0;

        // --- Date divider (root messages only) ---
        if (msg.timestamp && depth === 0) {
            const dateLabel = _dateLabelForTimestamp(msg.timestamp);
            if (dateLabel !== state._lastRenderedDate) {
                state._lastRenderedDate = dateLabel;
                const divEl = document.createElement("div");
                divEl.className = "date-divider";
                divEl.innerHTML = `<span>${dateLabel}</span>`;
                feed.appendChild(divEl);
            }
        }

        // --- Message grouping: only group with messages at the same depth ---
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
        // A11y: each message is an article with an accessible name of
        // "«sender», «time»" so a screen reader announces who/when before the
        // body content that follows (grouped messages hide the visual header but
        // keep this label so they're never anonymous under SR).
        div.setAttribute("role", "article");
        div.setAttribute("aria-label", msg.timestamp ? `${name}, ${timeAgo(msg.timestamp)}` : name);
        const avatarColor = webidColor(msg.from_webid);

        const presenceData = userPresence[msg.from_webid] || { status: "offline" };
        const presenceClass = presenceData.status === "online" ? "online" :
                              presenceData.status === "away" ? "away" :
                              presenceData.status === "busy" ? "busy" : "";

        const avatarBase = msg.from_avatar_b64
            ? `<img src="data:image/png;base64,${msg.from_avatar_b64}" class="avatar" alt="" style="width:40px;height:40px;border-radius:50%;">`
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
        const forwardBtn = `<button data-msg-action="forward" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.78rem;" title="${t('msg.forward')}">&#8599;</button>`;
        // R58: star an image attachment into the local GIF tray
        const _mimeForGif = (msg.file?.mime_type || '').toLowerCase();
        const saveGifBtn = (msg.file?.data_b64 && ['image/jpeg','image/png','image/gif','image/webp','image/avif'].includes(_mimeForGif))
            ? `<button data-msg-action="save-gif" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.85rem;" title="${t('gif.saveAction')}">&#9734;</button>`
            : "";

        // --- Avatar column ---
        const avatarCol = document.createElement("div");
        avatarCol.className = "msg-avatar-col";
        avatarCol.innerHTML = isGrouped
            ? `<span class="msg-compact-ts" title="${exactTs}">${compactTime}</span>`
            : avatarHtml;

        // --- Body column ---
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
                placeholder.innerHTML = `<span class="reply-connector"></span><em style="color:#8091a7">${t('msg.loadingReply')}</em>`;
                body.appendChild(placeholder);
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ cmd: "get_message", message_id: msg.reply_to_id }));
                }
            }
        }

        // Round 68: forwarded banner
        if (msg.forwarded) {
            body.innerHTML += `<div class="forwarded-banner">↗ ${t('msg.forwardedFrom', { name: escHtml(msg.forwarded_from_name || '') })}</div>`;
        }

        // Header: name + timestamp (first in group only)
        if (!isGrouped) {
            const suffixHtml = suffix ? `<span style="font-size:0.72em;color:#8091a7;margin-left:4px;font-weight:400;">·${suffix}</span>` : "";
            const botBadge = msg.is_bot ? `<span class="bot-badge">BOT</span>` : "";
            const importedBadge = msg.imported ? `<span style="font-size:0.7em;color:#94a3b8;background:#1e293b;border:1px solid #334155;border-radius:3px;padding:1px 5px;margin-left:6px;vertical-align:middle;">Imported</span>` : "";
            // R11.2.3: unverified shield for DID contacts not yet verified
            const isVerified = !msg.from_webid || msg.from_webid === selfWebId ||
                localStorage.getItem("proxion_verified_" + msg.from_webid) === "1";
            const shieldHtml = (!isVerified && msg.from_webid && msg.from_webid.startsWith("did:key:"))
                ? `<span title="${t('msg.identityUnverified')}" style="color:#8091a7;margin-left:4px;font-size:0.85em;">&#x1F6E1;</span>`
                : "";
            // R11.1.3: expiry countdown label
            let expireHtml = "";
            if (currentDisappearMs > 0 && msg.timestamp) {
                const expiresAt = new Date(msg.timestamp).getTime() + currentDisappearMs;
                expireHtml = `<span class="msg-expire-countdown" style="font-size:0.7em;color:#8091a7;margin-left:6px;" title="${t('msg.expires')}">⏱ ${_expireLabel(expiresAt - Date.now())}</span>`;
            }
            body.innerHTML += `<div class="msg-header"><span class="msg-sender" style="color:${avatarColor}">${escHtml(name)}${botBadge}${suffixHtml}${shieldHtml}</span><span class="msg-ts-header" title="${exactTs}">${timeAgo(msg.timestamp)}${importedBadge}${expireHtml}</span></div>`;
        }

        // Content
        const editedHtml = msg.edited_at
            ? `<span class="edited-badge" role="button" tabindex="0" data-msg-id="${msgId}" title="${t('msg.editHistory')}">${t('msg.edited')}</span>`
            : "";
        // Delivery tick rides inline at the end of the content's last line —
        // as a block-level sibling it used to cost every own message a whole
        // extra line just for a "✓".
        const receiptHtml = isOwn ? `<span class="read-receipt" data-msg-id="${msgId}">&#10003;</span>` : "";
        if (msg.content_type === "audio" && msg.audio_b64) {
            const _durSecs = msg.duration_ms ? Math.round(msg.duration_ms / 1000) : 0;
            const dur = _durSecs ? `<span class="audio-duration">${_durSecs}s</span>` : "";
            const _audioLabel = escHtml(t('msg.voiceFrom', { name }) + (_durSecs ? t('msg.voiceDuration', { secs: _durSecs }) : ""));
            body.innerHTML += `<div class="audio-message"><audio controls aria-label="${_audioLabel}" src="data:audio/webm;base64,${msg.audio_b64}"></audio>${dur}${receiptHtml}</div>`;
        } else {
            body.innerHTML += `<div class="msg-content"><span class="msg-text" dir="auto">${renderedText}</span>${editedHtml}${receiptHtml}</div>`;
        }

        if (fileHtml) body.innerHTML += fileHtml;
        body.innerHTML += `<div id="reactions-${msgId}" class="reactions"></div>`;

        // Hover action bar
        body.innerHTML += `<div class="msg-actions">
            <button data-msg-action="react" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.8rem;" title="${t('msg.react')}">+</button>
            <button data-msg-action="reply" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.85rem;" title="${t('msg.reply')}"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3"/></svg></button>
            ${editBtn}${deleteBtn}${forwardBtn}${saveGifBtn}
            <button data-msg-action="pin" data-msg-id="${msgId}" class="icon-btn" style="min-width:28px;min-height:28px;font-size:0.78rem;" title="${t('msg.pin')}"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0 1 11.186 0Z"/></svg></button>
        </div>`;

        div.appendChild(avatarCol);
        div.appendChild(body);
        div.addEventListener("contextmenu", e => openCtxMenu(e, msgId));
        feed.appendChild(div);
        renderReactions(msgId);
    }

    // C3: merge a batch of older messages (e.g. a federated /room-history page)
    // into the buffer — dedupe, keep chronological order, re-render the expanded
    // window and hold the scroll near the top so the user can keep paging up.
    // Returns the number of new messages actually merged.
    function mergeOlderHistory(olderMsgs) {
        const am = getAllMessages();
        const mm = getMessageMap();
        const seen = new Set(am.map(m => m.message_id));
        const older = (olderMsgs || []).filter(m => m && m.message_id && !seen.has(m.message_id));
        if (!older.length) return 0;
        const feed = document.getElementById("message-feed");
        const renderedCount = feed ? feed.querySelectorAll(".message").length : 0;
        older.forEach(m => { mm[m.message_id] = m; am.push(m); });
        am.sort((a, b) => (a.timestamp || "").localeCompare(b.timestamp || ""));
        if (feed) {
            feed.innerHTML = "";
            state._lastRenderedDate = null;
            _renderThreaded(am.slice(-(renderedCount + older.length)), feed);
            feed.scrollTop = 10;
        }
        return older.length;
    }

    // Virtual scroll + persistent history: load earlier messages on scroll to top.
    // Wired once via attach() so the #message-feed element exists.
    function attach() {
        document.getElementById("message-feed").addEventListener("scroll", (e) => {
            const allMessages = getAllMessages();
            const activeView = getActiveView();
            const socket = getSocket();
            const feed = e.target;
            // Hide scroll-to-bottom btn when user scrolls to bottom
            if (feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60) {
                state._scrollBottomUnread = 0;
                document.getElementById("scroll-bottom-btn").style.display = "none";
            }
            if (feed.scrollTop !== 0) return;
            // First expand in-memory buffer
            if (allMessages.length > RENDER_WINDOW) {
                const rendered = feed.querySelectorAll(".message").length;
                const totalLoaded = rendered + SCROLL_BATCH;
                const slice = allMessages.slice(-Math.min(totalLoaded, allMessages.length));
                feed.innerHTML = "";
                state._lastRenderedDate = null;
                _renderThreaded(slice, feed);
                feed.scrollTop = 10;
                return;
            }
            // C3: federated room (hosted on another gateway) — page older history
            // via the host's REST endpoint (the WS get_local_history path only
            // serves locally-stored rooms).
            const _isFedRoom = activeView && activeView.type === "room" && !activeView.local;
            if (_isFedRoom && !state._loadingOlderHistory) {
                const _code = getRoomCode ? getRoomCode(activeView.id) : "";
                const _oldest = allMessages[0];
                if (_code && _oldest && _oldest.timestamp) {
                    state._loadingOlderHistory = true;
                    fetch(`/room-history/${encodeURIComponent(activeView.id)}?code=${encodeURIComponent(_code)}&before=${encodeURIComponent(_oldest.timestamp)}&limit=${SCROLL_BATCH}`)
                        .then(r => r.ok ? r.json() : null)
                        .then(data => { state._loadingOlderHistory = false; mergeOlderHistory(data && data.messages); })
                        .catch(() => { state._loadingOlderHistory = false; });
                }
                return;
            }
            // Then fetch older messages from DB
            const _isCertDm = activeView && activeView.type === "dm";
            if (activeView && (activeView.local || _isCertDm) && !state._loadingOlderHistory
                    && socket && socket.readyState === WebSocket.OPEN) {
                const oldest = allMessages[0];
                if (oldest && oldest.timestamp) {
                    state._loadingOlderHistory = true;
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
    }

    return {
        renderMessages, renderMessage, _renderThreaded, scrollToBottom,
        _renderMessageEl, _insertReplyInFeed, _buildThreadedMessages, _dateLabelForTimestamp,
        mergeOlderHistory, attach, state,
    };
}
