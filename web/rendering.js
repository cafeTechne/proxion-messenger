// Message rendering — the core message feed renderer. Builds message elements
// in timestamp order (a reply carries an inline quote of its parent), draws
// date and "New messages" dividers, manages the
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

import { didSuffix, escHtml, webidColor, renderMarkdown, expireLabel as _expireLabel, b64attr } from './util.js';
import { parsePoll } from './polls.js';
import { applyRoomEmoji, getRoomEmoji } from './room-emoji.js';
import { t, tn, getLocale } from './i18n.js';
import { icon } from './icons.js';

// R59A: attachment kind by mime — pure, exported for tests. Video/audio get
// inline players (CSP already allows media-src data:), everything else falls
// through to the generic download row.
const _IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif']);
const _VIDEO_TYPES = new Set(['video/mp4', 'video/webm', 'video/quicktime', 'video/ogg']);
const _AUDIO_TYPES = new Set(['audio/mpeg', 'audio/ogg', 'audio/wav', 'audio/mp4', 'audio/flac']);
export function attachmentKind(mime) {
    const m = (mime || '').toLowerCase();
    if (_IMAGE_TYPES.has(m)) return 'image';
    if (_VIDEO_TYPES.has(m)) return 'video';
    if (_AUDIO_TYPES.has(m)) return 'audio';
    return 'file';
}

// Consecutive messages from one sender within this window share a header.
export const GROUP_WINDOW_MS = 5 * 60 * 1000;

// Clock time for a message header ("3:07 PM" / "15:07" by locale).
export function clockTime(ts) {
    const d = new Date(ts);
    if (isNaN(d)) return "";
    return d.toLocaleTimeString(getLocale(), { hour: "numeric", minute: "2-digit" });
}

// Full date and time, used as the hover title of a message time.
export function fullDateTime(ts) {
    const d = new Date(ts);
    if (isNaN(d)) return "";
    return d.toLocaleString(getLocale(), {
        weekday: "long", year: "numeric", month: "long", day: "numeric",
        hour: "numeric", minute: "2-digit",
    });
}

// <time> element for a message timestamp: clock time as text, the machine
// readable instant in datetime, the full date and time as the title.
export function timeHtml(ts, cls) {
    const d = new Date(ts);
    if (!ts || isNaN(d)) return "";
    return `<time class="${cls}" datetime="${escHtml(d.toISOString())}" title="${escHtml(fullDateTime(ts))}">${escHtml(clockTime(ts))}</time>`;
}

// Day separator label: Today / Yesterday, else "Monday, March 5", with the
// year added when it is not the current one.
export function dateLabel(ts, now = new Date()) {
    const d = new Date(ts);
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    if (d.toDateString() === now.toDateString()) return t('time.today');
    if (d.toDateString() === yesterday.toDateString()) return t('time.yesterday');
    const opts = { weekday: "long", month: "long", day: "numeric" };
    if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
    return d.toLocaleDateString(getLocale(), opts);
}

// Plain-text summary of a quoted parent message for a reply. Messages with no
// text fall back to "Photo" / the file name / "Voice message"; a deleted
// parent (tombstone) says so.
export function replySnippet(parent) {
    if (!parent) return "";
    if (parent.deleted) return t('msg.replyDeleted');
    const text = String(parent.content || "").replace(/\s+/g, " ").trim();
    if (text) return text.length > 200 ? text.slice(0, 200) + "…" : text;
    if (parent.file) {
        if (attachmentKind(parent.file.mime_type) === 'image') return t('msg.replyPhoto');
        const fn = String(parent.file.filename || "").replace(/[/\\]/g, "").trim();
        return fn || t('msg.replyAttachment');
    }
    if (parent.content_type === "audio") return t('msg.replyVoice');
    return "";
}

// Inner HTML of the one-line reply quote: author then snippet, both escaped.
export function replyQuoteHtml(parent) {
    if (parent && parent.deleted) {
        return `<span class="reply-snippet reply-deleted">${escHtml(replySnippet(parent))}</span>`;
    }
    const name = parent.from_display_name || (parent.from_webid || "").slice(0, 12);
    return `<b class="reply-author" style="color:${webidColor(parent.from_webid)}">${escHtml(name)}</b> <span class="reply-snippet">${escHtml(replySnippet(parent))}</span>`;
}

// Wrap @mentions in already-escaped message HTML. `names` are known display
// names (plain text) matched whole and case-insensitively, longest first, so
// names with spaces or non-ASCII letters work; anything else falls back to a
// single word of letters/digits/underscore.
export function highlightMentions(html, names = [], selfName = "") {
    const esc = [...new Set((names || []).filter(n => n && String(n).trim())
        .map(n => escHtml(String(n).trim())))]
        .sort((a, b) => b.length - a.length)
        .map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const word = "[\\p{L}\\p{N}_]";
    const alt = esc.length ? `(?:${esc.join("|")})(?!${word})|` : "";
    const re = new RegExp(`@(${alt}${word}+)`, "giu");
    const self = escHtml(String(selfName || "")).toLowerCase();
    return html.replace(re, (m, uname) =>
        `<span class="${self && uname.toLowerCase() === self ? "mention mention-self" : "mention"}">@${uname}</span>`);
}

// Scroll anchoring (pure, exported for tests). "At bottom" uses the same 60px
// slack as the scroll-to-bottom button logic.
export function isFeedAtBottom(feed) {
    return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
}

function _relTop(el, feed) {
    return el.getBoundingClientRect().top - feed.getBoundingClientRect().top;
}

// Record the first message visible in the feed viewport and its offset from
// the viewport top, so a re-render can put it back in the same place.
export function captureScrollAnchor(feed) {
    if (!feed) return null;
    const els = feed.querySelectorAll(".message[data-message-id]");
    for (const el of els) {
        const top = _relTop(el, feed);
        if (top + el.getBoundingClientRect().height > 0) {
            return { id: el.dataset.messageId, offset: top };
        }
    }
    return null;
}

// Scroll so the anchored message sits at the recorded offset again. Returns
// false when there is no anchor or it is no longer rendered.
export function restoreScrollAnchor(feed, anchor) {
    if (!feed || !anchor) return false;
    for (const el of feed.querySelectorAll(".message[data-message-id]")) {
        if (el.dataset.messageId === anchor.id) {
            feed.scrollTop += _relTop(el, feed) - anchor.offset;
            return true;
        }
    }
    return false;
}

export function createRendering({
    getActiveView, getSocket, getSelfWebId, getSelfPubHex,
    getCurrentDisappearMs, getMessageMap, getAllMessages, getUserPresence,
    renderReactions, openCtxMenu, sendUpdateLastRead,
    getRoomCode, renderWindow, scrollBatch,
    // Account-scopes the verification-badge localStorage key so a second account
    // on a shared browser can't read the first's trust marks (see e2e.js). Falls
    // back to identity for callers/tests that don't supply it.
    e2eScopedKey = (k) => k,
    // Display name for a webid ("" when unknown), and the active room's member
    // names: used to match @mentions of multi-word / non-ASCII names.
    resolveName = () => "",
    getMemberNames = () => [],
}) {
    const RENDER_WINDOW = renderWindow;
    const SCROLL_BATCH = scrollBatch;
    const state = {
        _lastRenderedDate: null,    // for date dividers (reset by view-switching)
        _scrollBottomUnread: 0,     // count of messages arrived while scrolled up
        _loadingOlderHistory: false,
        _pinnedToBottom: true,      // feed was at the bottom as of the last scroll
        _requestedReplies: new Set(), // reply parents already asked for via get_message
        _unreadBeforeId: null,      // "New messages" divider goes before this message
        _unreadSeen: false,         // reader reached the bottom since the divider appeared
    };

    function scrollToBottom() {
        const activeView = getActiveView();
        const feed = document.getElementById("message-feed");
        feed.scrollTop = feed.scrollHeight;
        state._pinnedToBottom = true;
        state._unreadSeen = true;
        state._scrollBottomUnread = 0;
        document.getElementById("scroll-bottom-btn").style.display = "none";
        if (activeView) sendUpdateLastRead(activeView.id);
    }

    let _readTimer = null;
    function _scheduleReadUpdate() {
        if (_readTimer || typeof setTimeout !== "function") return;
        _readTimer = setTimeout(() => {
            _readTimer = null;
            const v = getActiveView();
            if (v && (typeof document.hidden !== "boolean" || !document.hidden)) sendUpdateLastRead(v.id);
        }, 1500);
    }

    function _dateLabelForTimestamp(ts) {
        return dateLabel(ts);
    }

    // "N new messages" on the scroll-to-bottom button while arrivals pile up
    // below a reader who scrolled away.
    function _updateScrollBottomBtn() {
        const btn = document.getElementById("scroll-bottom-btn");
        const cnt = document.getElementById("scroll-bottom-count");
        const n = state._scrollBottomUnread;
        if (cnt) cnt.textContent = n > 0 ? tn('feed.newMessages', n) : "";
        if (btn) btn.style.display = "block";
    }

    function renderMessages() {
        const allMessages = getAllMessages();
        const feed = document.getElementById("message-feed");
        const slice = allMessages.slice(-RENDER_WINDOW);
        feed.innerHTML = "";
        state._lastRenderedDate = null;
        _renderThreaded(slice, feed);
        // Until the reader has reached the bottom once, a re-render keeps the
        // "New messages" divider in view instead of jumping past it.
        if (state._unreadSeen || !scrollToUnread()) {
            feed.scrollTop = feed.scrollHeight;
            state._pinnedToBottom = true;
        }
    }

    // --- "New messages" divider ---------------------------------------------
    // The divider is drawn by _renderMessageEl in front of the message whose id
    // is state._unreadBeforeId, so it survives re-renders of the feed. It goes
    // away once the reader has reached the bottom and then sends, or switches
    // to another conversation (resetUnread).

    // Pick the first message newer than lastReadTs (gateway seconds). Returns
    // its id, or null when nothing is unread / there is no read marker yet.
    function markUnread(messages, lastReadTs) {
        state._unreadBeforeId = null;
        state._unreadSeen = false;
        const cutoff = Number(lastReadTs) * 1000;
        if (!cutoff || !Array.isArray(messages)) return null;
        const first = messages.find(m => m && m.timestamp && new Date(m.timestamp).getTime() > cutoff);
        if (first) state._unreadBeforeId = first.message_id;
        return state._unreadBeforeId;
    }

    // Mark `msgId` as the first unread message (catch-up batch while open).
    function setUnreadBefore(msgId) {
        document.querySelectorAll("#message-feed .unread-divider").forEach(el => el.remove());
        state._unreadBeforeId = msgId || null;
        state._unreadSeen = false;
    }

    // Scroll so the divider sits near the top of the feed. Returns false when
    // there is no divider.
    function scrollToUnread() {
        const feed = document.getElementById("message-feed");
        const div = feed && feed.querySelector(".unread-divider");
        if (!div || typeof div.getBoundingClientRect !== "function") return false;
        feed.scrollTop += div.getBoundingClientRect().top - feed.getBoundingClientRect().top - 48;
        state._pinnedToBottom = isFeedAtBottom(feed);
        if (state._pinnedToBottom) state._unreadSeen = true;
        return true;
    }

    // Drop the divider once the reader has seen the bottom (or when forced).
    function clearUnreadDivider(force = false) {
        if (!force && !state._unreadSeen) return false;
        document.querySelectorAll("#message-feed .unread-divider").forEach(el => el.remove());
        state._unreadBeforeId = null;
        return true;
    }

    function resetUnread() {
        state._unreadBeforeId = null;
        state._unreadSeen = false;
    }

    // Re-render the feed with `slice` (used when older history is prepended)
    // while keeping the message the reader was looking at in the same place.
    // Falls back to preserving the distance from the bottom of the content.
    function rerenderKeepingAnchor(slice, feed) {
        const anchor = captureScrollAnchor(feed);
        const fromBottom = feed.scrollHeight - feed.scrollTop;
        feed.innerHTML = "";
        state._lastRenderedDate = null;
        _renderThreaded(slice, feed);
        if (!restoreScrollAnchor(feed, anchor)) {
            feed.scrollTop = feed.scrollHeight - fromBottom;
        }
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
        // Carry the client pod-partition timestamp across the server echo, so a later
        // edit/delete/seq addresses the day file the message was actually written to
        // (the echo's server clock can land on a different UTC day).
        const _prevPodTs = messageMap[msg.message_id]?.pod_ts;
        if (_prevPodTs && !msg.pod_ts) msg.pod_ts = _prevPodTs;
        messageMap[msg.message_id] = msg;
        // Only append DOM element if within the render window
        if (allMessages.length <= RENDER_WINDOW || allMessages.indexOf(msg) >= allMessages.length - RENDER_WINDOW) {
            const feed = document.getElementById("message-feed");
            // The "No messages yet." hero is added when a thread opens empty;
            // clear it the moment real content arrives, or it floats above the
            // messages forever (same stale-empty-state class as the sidebar CTA).
            feed.querySelector(".empty-state")?.remove();
            const atBottom = isFeedAtBottom(feed);
            // Every message, replies included, goes at the end in arrival
            // (timestamp) order; a reply shows an inline quote of its parent.
            const visibleMsgs = [...feed.querySelectorAll(".message[data-message-id]")];
            const lastEl = visibleMsgs[visibleMsgs.length - 1];
            const prev = lastEl ? messageMap[lastEl.dataset.messageId] : null;
            _renderMessageEl(msg, feed, prev);
            if (atBottom) {
                feed.scrollTop = feed.scrollHeight;
                state._pinnedToBottom = true;
                // Seen as it arrived: move the read marker (coalesced), so the
                // next open does not draw "New messages" above it.
                _scheduleReadUpdate();
            } else {
                // Scrolled up: show the scroll-to-bottom button with a count
                state._scrollBottomUnread++;
                _updateScrollBottomBtn();
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

    // Renders `messages` in the given (chronological) order into `feed`,
    // tracking prev for grouping. Replies are not moved under their parent:
    // a live reply to an old message must land at the bottom where it is seen.
    function _renderThreaded(messages, feed) {
        let prev = null;
        messages.forEach(msg => { _renderMessageEl(msg, feed, prev); prev = msg; });
    }

    function _renderMessageEl(msg, feed, prevInThread, opts = {}) {
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
        // message_id is client-supplied and relayed verbatim by the gateway, so
        // it is attacker-controlled. The div.id / setAttribute / dataset writes
        // below are safe, but every interpolation of it into an innerHTML string
        // must be escaped or a crafted id breaks out of the attribute (stored XSS).
        const msgIdEsc = escHtml(msgId);
        messageMap[msgId] = msg;
        // --- Date divider (every message, replies included) ---
        let dividerEmitted = false;
        if (msg.timestamp && !opts.noDivider) {
            const label = _dateLabelForTimestamp(msg.timestamp);
            if (label !== state._lastRenderedDate) {
                state._lastRenderedDate = label;
                dividerEmitted = true;
                const divEl = document.createElement("div");
                divEl.className = "date-divider";
                divEl.innerHTML = `<span>${escHtml(label)}</span>`;
                feed.appendChild(divEl);
            }
        }
        // --- "New messages" divider in front of the first unread message ---
        if (!opts.noDivider && state._unreadBeforeId && msgId === state._unreadBeforeId) {
            dividerEmitted = true;
            const unreadEl = document.createElement("div");
            unreadEl.className = "unread-divider";
            unreadEl.setAttribute("role", "separator");
            unreadEl.innerHTML = `<span>${escHtml(t('feed.unreadDivider'))}</span>`;
            feed.appendChild(unreadEl);
        }

        // --- Message grouping: same sender within GROUP_WINDOW_MS, never
        // across a divider, and a reply always starts its own group so its
        // quote sits above a visible sender line ---
        const isGrouped = !!(prevInThread && !dividerEmitted && !msg.reply_to_id &&
            msg.from_webid && msg.from_webid !== "unknown" &&
            prevInThread.from_webid === msg.from_webid &&
            msg.timestamp && prevInThread.timestamp &&
            (new Date(msg.timestamp) - new Date(prevInThread.timestamp)) < GROUP_WINDOW_MS);

        const div = document.createElement("div");
        div.id = `msg-${msgId}`;
        div.setAttribute("data-message-id", msgId);
        div.dataset.fromWebid = msg.from_webid || "";
        div.className = "message" + (isGrouped ? " msg-grouped" : "");
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
        div.setAttribute("aria-label", msg.timestamp ? `${name}, ${fullDateTime(msg.timestamp)}` : name);
        const avatarColor = webidColor(msg.from_webid);

        const presenceData = userPresence[msg.from_webid] || { status: "offline" };
        const presenceClass = presenceData.status === "online" ? "online" :
                              presenceData.status === "away" ? "away" :
                              presenceData.status === "busy" ? "busy" : "";

        const avatarBase = msg.from_avatar_b64
            ? `<img src="data:image/png;base64,${b64attr(msg.from_avatar_b64)}" class="avatar" alt="" style="width:40px;height:40px;border-radius:50%;">`
            : `<div class="avatar placeholder" style="background:${avatarColor};width:40px;height:40px;line-height:40px;font-size:16px;font-weight:bold;text-align:center;border-radius:50%;">${(name[0] || "?").toUpperCase()}</div>`;
        const presenceDot = `<div class="avatar-presence ${presenceClass}" title="${escHtml(presenceData.status || '')}" style="bottom:-1px;right:-1px;"></div>`;
        const avatarHtml = `<div style="position:relative;display:inline-block;cursor:pointer;" data-profile-avatar data-msg-action="profile" data-webid="${escHtml(msg.from_webid || '')}" data-name="${escHtml(name)}">${avatarBase}${presenceDot}</div>`;

        // Render text with Markdown and mention highlighting
        let rawText = msg.snippet || msg.content || "";
        const selfDisplayName = localStorage.getItem("proxion_display_name") || "";
        const mentionsMe = (msg.mentions && selfWebId && msg.mentions.includes(selfWebId)) ||
            (selfDisplayName && rawText.toLowerCase().includes("@" + selfDisplayName.toLowerCase()));
        if (mentionsMe) div.classList.add("mention-highlight");
        // Known names (the message's explicit mentions, room members, ourselves)
        // match whole, so "@Ana María" and other multi-word or non-ASCII names
        // highlight in full.
        const _mentionNames = [selfDisplayName, ...getMemberNames(),
            ...(Array.isArray(msg.mentions) ? msg.mentions.map(w => resolveName(w)) : [])];
        let renderedText = highlightMentions(renderMarkdown(rawText), _mentionNames, selfDisplayName);
        // R59G: replace :name: tokens with this room's custom emoji (post-escape,
        // map-driven — unknown names pass through untouched).
        renderedText = applyRoomEmoji(renderedText, getRoomEmoji(msg.thread_id || activeView?.id || ''));

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
            const _kind = attachmentKind(_mime);
            // Wire-supplied base64 goes into innerHTML src=/href= attributes, so it
            // MUST pass through b64attr (strips anything outside the base64 alphabet)
            // exactly like the avatar/voice-note paths, or a crafted data_b64 breaks
            // out of the attribute. The truthiness guards below keep the raw field.
            const _b64 = b64attr(msg.file.data_b64 || '');
            const _dlLink = `<a href="data:application/octet-stream;base64,${_b64}" download="${safeFilename}"
                       style="color:var(--accent-text);font-size:0.8em;display:block;margin-top:3px;">${t('file.downloadNamed', { filename: safeFilename })}</a>`;
            if (_kind === 'image' && msg.file.data_b64) {
                // R13.7: inline image preview (+R60C: sender-marked spoiler
                // renders blurred under a reveal cover — one-way, like text
                // spoilers; the reveal handler lives in the feed delegation)
                const _imgSrc = `data:${_mime};base64,${_b64}`;
                const _img = `<img class="msg-image-preview" src="${_imgSrc}" alt="${safeFilename}" loading="lazy" tabindex="0" role="button">`;
                fileHtml = msg.file.spoiler === true
                    ? `<div class="attachment">
                        <div class="media-spoiler" role="button" tabindex="0" aria-label="${t('spoiler.reveal')}">
                          ${_img}<span class="media-spoiler-label">${t('spoiler.mediaLabel')}</span>
                        </div>${_dlLink}</div>`
                    : `<div class="attachment">${_img}${_dlLink}</div>`;
            } else if (_kind === 'video' && msg.file.data_b64) {
                // R59A: inline video player (short clips are the modern GIF)
                fileHtml = `<div class="attachment">
                    <video controls preload="metadata" class="msg-video-preview" aria-label="${safeFilename}"
                           src="data:${_mime};base64,${_b64}"></video>
                    ${_dlLink}</div>`;
            } else if (_kind === 'audio' && msg.file.data_b64) {
                // R59A: inline audio player for music/sound attachments
                fileHtml = `<div class="attachment">
                    <audio controls class="msg-audio-preview" aria-label="${safeFilename}"
                           src="data:${_mime};base64,${_b64}"></audio>
                    <span style="font-size:0.8em;color:#8091a7;display:block;margin-top:2px;">${safeFilename}</span>
                    ${_dlLink}</div>`;
            } else {
                // Force octet-stream to prevent data URI MIME injection
                fileHtml = `<div class="attachment">${icon('paperclip', { size: 18 })} ${safeFilename} (${Math.round(msg.file.size/1024)} KB)
                    <a href="data:application/octet-stream;base64,${_b64}" download="${safeFilename}"
                       style="color:var(--accent-text);margin-left:10px;">${t('file.download')}</a></div>`;
            }
        }

        const isOwn = (msg.own === true) ||
            (selfWebId && msg.from_webid === selfWebId) ||
            (selfPubHex && msg.from_pub_hex === selfPubHex);

        // Hover bar keeps the frequent actions (react, reply, own-message edit)
        // and a More button that opens the message context menu, which holds
        // the rest (copy, forward, pin, save, save GIF, meme, delete).
        const editBtn = isOwn
            ? `<button data-msg-action="edit" data-msg-id="${msgIdEsc}" class="icon-btn" aria-label="${t('msg.edit')}" title="${t('msg.edit')}">${icon('edit')}</button>`
            : "";

        // --- Avatar column ---
        const avatarCol = document.createElement("div");
        avatarCol.className = "msg-avatar-col";
        avatarCol.innerHTML = isGrouped
            ? timeHtml(msg.timestamp, "msg-compact-ts")
            : avatarHtml;

        // --- Body column ---
        const body = document.createElement("div");
        body.className = "msg-body";

        // Inline reply quote: one muted line above the message, click jumps to
        // the parent (scroll-reply). A deleted parent leaves a tombstone in
        // messageMap, which renders as "Original message was deleted".
        if (msg.reply_to_id) {
            const parent = messageMap[msg.reply_to_id];
            const replyIdEsc = escHtml(msg.reply_to_id);
            if (parent) {
                body.innerHTML += `<div class="reply-context" data-msg-action="scroll-reply" data-reply-id="${replyIdEsc}">${replyQuoteHtml(parent)}</div>`;
            } else {
                // Parent not in the buffer: fetch it once, fill the quote when it arrives
                body.innerHTML += `<div class="reply-context reply-context-loading" data-msg-action="scroll-reply" data-reply-id="${replyIdEsc}" data-reply-target="${replyIdEsc}"><em>${t('msg.loadingReply')}</em></div>`;
                if (!state._requestedReplies.has(msg.reply_to_id)
                        && socket && socket.readyState === WebSocket.OPEN) {
                    state._requestedReplies.add(msg.reply_to_id);
                    socket.send(JSON.stringify({ cmd: "get_message", message_id: msg.reply_to_id }));
                }
            }
        }

        // Round 68: forwarded banner
        if (msg.forwarded) {
            body.innerHTML += `<div class="forwarded-banner">${icon('forward', { size: 12 })} ${t('msg.forwardedFrom', { name: escHtml(msg.forwarded_from_name || '') })}</div>`;
        }

        // Header: name + timestamp (first in group only)
        if (!isGrouped) {
            const suffixHtml = suffix ? `<span style="font-size:0.72em;color:#8091a7;margin-left:4px;font-weight:400;">·${suffix}</span>` : "";
            const botBadge = msg.is_bot ? `<span class="bot-badge">BOT</span>` : "";
            const importedBadge = msg.imported ? `<span style="font-size:0.7em;color:#94a3b8;background:#1e293b;border:1px solid #334155;border-radius:3px;padding:1px 5px;margin-left:6px;vertical-align:middle;">Imported</span>` : "";
            // R11.2.3: unverified shield for DID contacts not yet verified
            const isVerified = !msg.from_webid || msg.from_webid === selfWebId ||
                localStorage.getItem(e2eScopedKey("proxion_verified_" + msg.from_webid)) === "1";
            const shieldHtml = (!isVerified && msg.from_webid && msg.from_webid.startsWith("did:key:"))
                ? `<span title="${t('msg.identityUnverified')}" style="color:#8091a7;margin-left:4px;font-size:0.85em;">&#x1F6E1;</span>`
                : "";
            // R107 / #4: an incoming DM or Long Chat room message whose signature
            // did not verify against the author's published identity — surfaced, not
            // blocked. The sender_verified===false flag is set only on the paths that
            // carry a proof (gateway-free DM single + fanout, and pod-read room
            // messages), so it already scopes this to those messages.
            const dmAuthHtml = (msg.sender_verified === false && msg.from_webid && msg.from_webid !== selfWebId)
                ? `<span title="${t('msg.senderUnverified')}" style="color:#e0a458;margin-left:4px;font-size:0.85em;">&#x26A0;&#xFE0F;</span>`
                : "";
            // R11.1.3: expiry countdown label
            let expireHtml = "";
            if (currentDisappearMs > 0 && msg.timestamp) {
                const expiresAt = new Date(msg.timestamp).getTime() + currentDisappearMs;
                expireHtml = `<span class="msg-expire-countdown" style="font-size:0.7em;color:#8091a7;margin-left:6px;" title="${t('msg.expires')}">${icon('clock', { size: 12 })} <span class="msg-expire-label">${_expireLabel(expiresAt - Date.now())}</span></span>`;
            }
            body.innerHTML += `<div class="msg-header"><span class="msg-sender" style="color:${avatarColor}">${escHtml(name)}${botBadge}${suffixHtml}${shieldHtml}${dmAuthHtml}</span><span class="msg-ts-header">${timeHtml(msg.timestamp, "msg-ts-time")}${importedBadge}${expireHtml}</span></div>`;
        }

        // Content
        const editedHtml = msg.edited_at
            ? `<span class="edited-badge" role="button" tabindex="0" data-msg-id="${msgIdEsc}" title="${t('msg.editHistory')}">${t('msg.edited')}</span>`
            : "";
        // Delivery tick rides inline at the end of the content's last line —
        // as a block-level sibling it used to cost every own message a whole
        // extra line just for a "✓".
        const receiptHtml = isOwn ? `<span class="read-receipt" data-msg-id="${msgIdEsc}">${icon('check', { size: 12 })}<span class="sr-only">${t('receipt.sent')}</span></span>` : "";
        const _poll = parsePoll(rawText);
        if (msg.content_type === "audio" && msg.audio_b64) {
            const _durSecs = msg.duration_ms ? Math.round(msg.duration_ms / 1000) : 0;
            const dur = _durSecs ? `<span class="audio-duration">${_durSecs}s</span>` : "";
            const _audioLabel = escHtml(t('msg.voiceFrom', { name }) + (_durSecs ? t('msg.voiceDuration', { secs: _durSecs }) : ""));
            body.innerHTML += `<div class="audio-message"><audio controls aria-label="${_audioLabel}" src="data:audio/webm;base64,${b64attr(msg.audio_b64)}"></audio>${dur}${receiptHtml}</div>`;
        } else if (_poll) {
            // R59F: poll card — plain text on the wire, upgraded rendering here.
            // The tally IS the reaction row below (auto-seeded keycaps), so
            // counts live-update through the existing reaction pipeline.
            const rows = _poll.options.map(o =>
                `<div class="poll-opt">${o.emoji} ${escHtml(o.text)}</div>`).join('');
            body.innerHTML += `<div class="msg-content"><div class="poll-card" dir="auto">
                <div class="poll-q">${escHtml(_poll.question)}</div>${rows}
                <div class="poll-hint">${t('poll.voteHint')}</div></div>${editedHtml}${receiptHtml}</div>`;
        } else {
            body.innerHTML += `<div class="msg-content"><span class="msg-text" dir="auto">${renderedText}</span>${editedHtml}${receiptHtml}</div>`;
        }

        if (fileHtml) body.innerHTML += fileHtml;
        body.innerHTML += `<div id="reactions-${msgIdEsc}" class="reactions"></div>`;

        // Hover action bar
        body.innerHTML += `<div class="msg-actions">
            <button data-msg-action="react" data-msg-id="${msgIdEsc}" class="icon-btn" aria-label="${t('msg.react')}" title="${t('msg.react')}">${icon('face-smile')}</button>
            <button data-msg-action="reply" data-msg-id="${msgIdEsc}" class="icon-btn" aria-label="${t('msg.reply')}" title="${t('msg.reply')}">${icon('reply')}</button>
            ${editBtn}
            <button data-msg-action="more" data-msg-id="${msgIdEsc}" class="icon-btn" aria-label="${t('ui.moreActions')}" title="${t('ui.moreActions')}" aria-haspopup="true">${icon('ellipsis-horizontal')}</button>
        </div>`;

        div.appendChild(avatarCol);
        div.appendChild(body);
        div.addEventListener("contextmenu", e => openCtxMenu(e, msgId));
        feed.appendChild(div);
        renderReactions(msgId);
    }

    // C3: merge a batch of older messages (e.g. a federated /room-history page)
    // into the buffer — dedupe, keep chronological order, re-render the expanded
    // window and keep the message the reader was on anchored in place.
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
        if (feed) rerenderKeepingAnchor(am.slice(-(renderedCount + older.length)), feed);
        return older.length;
    }

    // Virtual scroll + persistent history: load earlier messages on scroll to top.
    // Wired once via attach() so the #message-feed element exists.
    function attach() {
        const _feedEl = document.getElementById("message-feed");
        // Images and video posters have no intrinsic size until they load, so a
        // feed pinned to the bottom ends up short of it once they grow. Re-stick
        // on load, but only if the reader had not scrolled away. load does not
        // bubble, hence the capture listener.
        const _restick = (e) => {
            const tag = e.target && e.target.tagName;
            if ((tag === "IMG" || tag === "VIDEO") && state._pinnedToBottom) {
                _feedEl.scrollTop = _feedEl.scrollHeight;
            }
        };
        _feedEl.addEventListener("load", _restick, true);
        _feedEl.addEventListener("loadedmetadata", _restick, true);
        _feedEl.addEventListener("scroll", (e) => {
            const allMessages = getAllMessages();
            const activeView = getActiveView();
            const socket = getSocket();
            const feed = e.target;
            state._pinnedToBottom = isFeedAtBottom(feed);
            // Hide scroll-to-bottom btn when user scrolls to bottom
            if (state._pinnedToBottom) {
                state._scrollBottomUnread = 0;
                state._unreadSeen = true;
                document.getElementById("scroll-bottom-btn").style.display = "none";
            }
            if (feed.scrollTop !== 0) return;
            // First expand the in-memory buffer, but only while it holds
            // messages that are not rendered yet. Once everything buffered is on
            // screen, fall through and ask for older history.
            const rendered = feed.querySelectorAll(".message").length;
            if (allMessages.length > rendered) {
                const totalLoaded = rendered + SCROLL_BATCH;
                const slice = allMessages.slice(-Math.min(totalLoaded, allMessages.length));
                rerenderKeepingAnchor(slice, feed);
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

    // Remove a message from the feed. If it headed a group, the next grouped
    // message becomes the head (gets the name and avatar back). With
    // `tombstone`, messageMap keeps a { deleted: true } stub so replies that
    // quote it say "Original message was deleted" instead of re-fetching it.
    function removeMessage(msgId, { tombstone = true } = {}) {
        const messageMap = getMessageMap();
        const el = document.getElementById(`msg-${msgId}`);
        if (el) {
            const wasHead = !el.classList.contains("msg-grouped");
            const next = el.nextElementSibling;
            el.remove();
            if (wasHead && next && next.classList && next.classList.contains("msg-grouped")) {
                _promoteToGroupHead(next);
            }
        }
        const old = messageMap[msgId];
        if (tombstone) {
            messageMap[msgId] = { message_id: msgId, deleted: true,
                thread_id: old?.thread_id, timestamp: old?.timestamp };
            const sel = `.reply-context[data-reply-id="${typeof CSS !== "undefined" && CSS.escape ? CSS.escape(msgId) : msgId}"]`;
            document.querySelectorAll(sel).forEach(q => {
                q.classList.remove("reply-context-loading");
                q.innerHTML = replyQuoteHtml(messageMap[msgId]);
            });
        } else {
            delete messageMap[msgId];
        }
    }

    // Give a grouped message its own header and avatar, keeping the element
    // (and anything attached to it later, like a link preview) in place.
    function _promoteToGroupHead(el) {
        const msgId = el.dataset.messageId;
        const msg = msgId && getMessageMap()[msgId];
        if (!msg) { el.classList.remove("msg-grouped"); return; }
        const tmp = document.createElement("div");
        el.id = "";   // let _renderMessageEl build a fresh copy
        try { _renderMessageEl(msg, tmp, null, { noDivider: true }); }
        finally { el.id = `msg-${msgId}`; }
        const fresh = tmp.firstElementChild;
        el.classList.remove("msg-grouped");
        if (!fresh) return;
        const freshAvatar = fresh.querySelector(".msg-avatar-col");
        const oldAvatar = el.querySelector(".msg-avatar-col");
        if (freshAvatar && oldAvatar) oldAvatar.replaceWith(freshAvatar);
        const header = fresh.querySelector(".msg-header");
        const body = el.querySelector(".msg-body");
        if (header && body && !body.querySelector(".msg-header")) {
            const before = [...body.children].find(c =>
                !c.classList.contains("reply-context") && !c.classList.contains("forwarded-banner"));
            body.insertBefore(header, before || null);
        }
    }

    return {
        renderMessages, renderMessage, _renderThreaded, scrollToBottom,
        _renderMessageEl, _dateLabelForTimestamp,
        markUnread, setUnreadBefore, scrollToUnread, clearUnreadDivider, resetUnread,
        removeMessage,
        mergeOlderHistory, rerenderKeepingAnchor, attach, state,
    };
}
