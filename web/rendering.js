// Message rendering: the core message feed renderer. Builds message elements
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

import { didIdTag, escHtml, presenceLabel, webidColor, avatarInitial, renderMarkdown, expireLabel as _expireLabel, b64attr, formatFileSize, gatewayUrl } from './util.js';
import { parsePoll } from './polls.js';
import { applyRoomEmoji, getRoomEmoji } from './room-emoji.js';
import { t, tn, getLocale } from './i18n.js';
import { icon } from './icons.js';
import { normalizeMediaDims } from './media-resize.js';
import { isUndecryptable } from './e2e-secondary.js';
import { isAnimatedImage, initialGifState, gifToggleHtml } from './gifmotion.js';

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
    // A parent this device could not open: the notice, never its ciphertext.
    if (parent.e2e === true || isUndecryptable(parent.content)) return t('e2e.undecryptable');
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

// width/height attributes from an attachment's optional wire dimensions, plus
// the same numbers as custom properties that style.css turns into a fitted
// width and aspect-ratio, so the feed reserves the media box before it
// decodes. Untrusted input: only integers in range get through, as numbers.
export function mediaSizeAttrs(file) {
    const d = normalizeMediaDims(file && file.width, file && file.height);
    return d ? ` width="${d.width}" height="${d.height}" style="--media-w: ${d.width}; --media-h: ${d.height}"` : '';
}

// The gateway's text for a file message ("📎" or "📎 name"). The attachment
// card already shows the file, so this caption is not rendered as text.
export function isFileCaption(msg) {
    return !!(msg && msg.file) && String(msg.content || '').trim().startsWith('📎');
}

// Attachment card for a message's file (pure, exported for tests). Three
// states: the bytes are here (preview / player / download), the bytes are
// being fetched from the gateway (a stored room file in history), or the file
// is not available on this device (never a bare filename).
export function attachmentHtml(file) {
    if (!file) return '';
    // Strip path-traversal sequences before using filename in download attribute.
    // escHtml handles XSS; this strips directory components so the OS/browser
    // cannot be confused into writing outside the Downloads folder.
    const _rawFilename = String(file.filename || 'file')
        .replace(/[/\\]/g, '')       // remove / and \
        .replace(/\.\./g, '')         // remove ..
        .trim() || 'file';
    const safeFilename = escHtml(_rawFilename);
    if (!file.data_b64) {
        const loading = file.stored === true && file.unavailable !== true;
        const note = loading ? t('file.loading') : t('file.unavailable');
        return `<div class="attachment ${loading ? 'attachment-loading' : 'attachment-unavailable'}">${icon('paperclip', { size: 18 })} <span class="attachment-name">${safeFilename}</span> <span class="attachment-note"${loading ? '' : ' role="note"'}>${escHtml(note)}</span></div>`;
    }
    const _mime = String(file.mime_type || '').toLowerCase();
    const _kind = attachmentKind(_mime);
    // Wire-supplied base64 goes into innerHTML src=/href= attributes, so it
    // MUST pass through b64attr (strips anything outside the base64 alphabet)
    // exactly like the avatar/voice-note paths, or a crafted data_b64 breaks
    // out of the attribute.
    const _b64 = b64attr(file.data_b64);
    // The link reads "Download"; the accessible name keeps the filename.
    const _dlLink = `<a href="data:application/octet-stream;base64,${_b64}" download="${safeFilename}"
               aria-label="${escHtml(t('file.downloadNamed', { filename: _rawFilename }))}"
               class="attachment-download">${t('file.download')}</a>`;
    if (_kind === 'image') {
        // R13.7: inline image preview (+R60C: sender-marked spoiler
        // renders blurred under a reveal cover — one-way, like text
        // spoilers; the reveal handler lives in the feed delegation)
        const _imgSrc = `data:${_mime};base64,${_b64}`;
        // A real button wraps the preview (it opens the lightbox). Under a
        // spoiler cover it stays out of the tab order until revealed.
        const _spoiler = file.spoiler === true;
        let _img = `<button type="button" class="msg-image-btn"${_spoiler ? ' tabindex="-1"' : ''}><img class="msg-image-preview" src="${_imgSrc}" alt="${safeFilename}" loading="lazy"${mediaSizeAttrs(file)}></button>`;
        // Animated GIF/WebP: a play/pause control (WCAG 2.2.2), paused
        // from the start under prefers-reduced-motion.
        if (isAnimatedImage(_mime, file.data_b64)) {
            const _gifState = initialGifState();
            _img = `<span class="gif-wrap" data-gif="${_gifState}">${_img}${gifToggleHtml(_gifState)}</span>`;
        }
        return _spoiler
            ? `<div class="attachment">
                <div class="media-spoiler" role="button" tabindex="0" aria-label="${t('spoiler.reveal')}">
                  ${_img}<span class="media-spoiler-label">${t('spoiler.mediaLabel')}</span>
                </div>${_dlLink}</div>`
            : `<div class="attachment">${_img}${_dlLink}</div>`;
    }
    if (_kind === 'video') {
        // R59A: inline video player (short clips are the modern GIF)
        return `<div class="attachment">
            <video controls preload="metadata" class="msg-video-preview" aria-label="${safeFilename}"${mediaSizeAttrs(file)}
                   src="data:${_mime};base64,${_b64}"></video>
            ${_dlLink}</div>`;
    }
    if (_kind === 'audio') {
        // R59A: inline audio player for music/sound attachments
        return `<div class="attachment">
            <audio controls class="msg-audio-preview" aria-label="${safeFilename}"
                   src="data:${_mime};base64,${_b64}"></audio>
            <span class="attachment-name">${safeFilename}</span>
            ${_dlLink}</div>`;
    }
    // Force octet-stream to prevent data URI MIME injection
    return `<div class="attachment">${icon('paperclip', { size: 18 })} ${safeFilename} (${escHtml(formatFileSize(file.size))})
            <a href="data:application/octet-stream;base64,${_b64}" download="${safeFilename}"
               class="attachment-download attachment-download--inline">${t('file.download')}</a></div>`;
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
    const byId = typeof document !== "undefined" && document.getElementById
        ? document.getElementById(`msg-${anchor.id}`) : null;
    if (byId && byId.parentNode === feed) {
        feed.scrollTop += _relTop(byId, feed) - anchor.offset;
        return true;
    }
    for (const el of feed.querySelectorAll(".message[data-message-id]")) {
        if (el.dataset.messageId === anchor.id) {
            feed.scrollTop += _relTop(el, feed) - anchor.offset;
            return true;
        }
    }
    return false;
}

// Feed rows that are messages, and the dividers drawn in front of one.
function _isMsgEl(el) {
    return !!(el && el.classList && el.classList.contains("message") && el.dataset && el.dataset.messageId);
}
function _isDivider(el) {
    return !!(el && el.classList && (el.classList.contains("date-divider") || el.classList.contains("unread-divider")));
}
function _firstMsgEl(feed) {
    let el = feed && feed.firstElementChild;
    while (el && !_isMsgEl(el)) el = el.nextElementSibling;
    return el || null;
}
function _lastMsgEl(feed) {
    let el = feed && feed.lastElementChild;
    while (el && !_isMsgEl(el)) el = el.previousElementSibling;
    return el || null;
}
// The first of the dividers directly in front of `el` (or `el` itself).
function _dividerRunStart(el) {
    let start = el;
    while (start.previousElementSibling && _isDivider(start.previousElementSibling)) start = start.previousElementSibling;
    return start;
}

// Which message ids a buffer holds, so renderMessage dedupes in O(1). The
// host reassigns allMessages on view switches and pushes into it in a few
// places, so the index is keyed on the array and rebuilt when its length
// moved without us.
const _bufferIndex = new WeakMap();
function _bufferIds(arr) {
    let e = _bufferIndex.get(arr);
    if (!e || e.len !== arr.length) {
        e = { ids: new Set(arr.map(m => m && m.message_id)), len: arr.length };
        _bufferIndex.set(arr, e);
    }
    return e;
}

// When a message disappears (ms since epoch), or null when it does not: no
// timer, or it was sent before the timer was turned on (`since`).
export function messageExpiresAt(timestamp, ms, since = '') {
    if (!(ms > 0) || !timestamp) return null;
    const ts = new Date(timestamp).getTime();
    if (!Number.isFinite(ts)) return null;
    if (since) {
        const start = new Date(since).getTime();
        if (Number.isFinite(start) && ts < start) return null;
    }
    return ts + ms;
}

export function createRendering({
    getActiveView, getSocket, getSelfWebId, getSelfPubHex,
    getCurrentDisappearMs, getCurrentDisappearSince = () => '', getMessageMap, getAllMessages, getUserPresence,
    renderReactions, openCtxMenu, sendUpdateLastRead,
    getRoomCode, renderWindow, scrollBatch,
    // Account-scopes the verification-badge localStorage key so a second account
    // on a shared browser can't read the first's trust marks (see e2e.js). Falls
    // back to identity for callers/tests that don't supply it.
    e2eScopedKey = (k) => k,
    // Whether the user verified this contact's safety number (e2e.js).
    isPeerVerified = () => false,
    // Display name for a webid ("" when unknown), and the active room's member
    // names: used to match @mentions of multi-word / non-ASCII names.
    resolveName = () => "",
    getMemberNames = () => [],
    // Most messages kept in the DOM. Paging past it drops rows from the far
    // end; scrolling back renders them again from the buffer.
    maxRendered = renderWindow * 3,
}) {
    const RENDER_WINDOW = renderWindow;
    const SCROLL_BATCH = scrollBatch;
    const MAX_RENDERED = maxRendered;
    const state = {
        _lastRenderedDate: null,    // for date dividers (reset by view-switching)
        _scrollBottomUnread: 0,     // count of messages arrived while scrolled up
        _loadingOlderHistory: false,
        _pinnedToBottom: true,      // feed was at the bottom as of the last scroll
        _requestedReplies: new Set(), // reply parents already asked for via get_message
        _unreadBeforeId: null,      // "New messages" divider goes before this message
        _unreadSeen: false,         // reader reached the bottom since the divider appeared
        _unreadEnteredFor: null,    // divider id that already played its entry fade
        _requestedFiles: new Set(), // stored room files already asked for via get_files
        _detachedTail: false,       // newest messages were dropped from the DOM (reader is far up)
    };

    // The rendered rows end before the newest buffered message.
    function _tailDetached(feed) {
        if (!state._detachedTail) return false;
        if (!_firstMsgEl(feed)) { state._detachedTail = false; return false; }
        return true;
    }

    // The copy of a buffered message to draw: messageMap holds edits and the
    // latest echo, a deleted or expired one is not drawn at all.
    function _current(m) {
        const cur = getMessageMap()[m.message_id];
        if (cur && cur.deleted) return null;
        const msg = cur && cur.message_id === m.message_id ? cur : m;
        const exp = messageExpiresAt(msg.timestamp, getCurrentDisappearMs(), getCurrentDisappearSince());
        if (exp !== null && exp <= Date.now()) return null;
        return msg;
    }

    // Bulk feed changes: keep the log quiet for screen readers while rows
    // are inserted or dropped (a no-op when the caller already marked it).
    function _busy(feed, fn) {
        const owned = typeof feed.getAttribute === "function" && typeof feed.setAttribute === "function"
            && !feed.getAttribute("aria-busy");
        if (owned) feed.setAttribute("aria-busy", "true");
        try { return fn(); } finally {
            if (owned) {
                const clear = () => { if (typeof feed.removeAttribute === "function") feed.removeAttribute("aria-busy"); };
                if (typeof setTimeout === "function") setTimeout(clear, 50); else clear();
            }
        }
    }

    // Stored room files in history come without their bytes. Ids rendered in
    // one pass are sent together (get_files takes up to 20 per command).
    let _fileQueue = [];
    function _requestFile(msgId) {
        if (state._requestedFiles.has(msgId)) return;
        state._requestedFiles.add(msgId);
        _fileQueue.push(msgId);
        if (_fileQueue.length > 1) return;
        const flush = () => {
            const ids = _fileQueue;
            _fileQueue = [];
            const socket = getSocket();
            if (!socket || socket.readyState !== WebSocket.OPEN) {
                // Not connected: let a later render ask again.
                ids.forEach(id => state._requestedFiles.delete(id));
                return;
            }
            for (let i = 0; i < ids.length; i += 20) {
                socket.send(JSON.stringify({ cmd: "get_files", message_ids: ids.slice(i, i + 20) }));
            }
        };
        if (typeof queueMicrotask === "function") queueMicrotask(flush); else setTimeout(flush, 0);
    }

    // Apply a get_files answer: `file` carries the bytes, or null when the
    // gateway no longer has the file. Redraws only the attachment card.
    function fillAttachment(msgId, file) {
        // Answered: a later render (after a view switch dropped this copy) may ask again.
        state._requestedFiles.delete(msgId);
        const msg = getMessageMap()[msgId];
        if (!msg || !msg.file) return;
        msg.file = file && file.data_b64
            ? { ...msg.file, ...file, stored: true }
            : { ...msg.file, unavailable: true };
        const card = document.getElementById(`msg-${msgId}`)?.querySelector(".attachment");
        // Keep the id the message's aria-describedby points at.
        if (card) card.outerHTML = attachmentHtml(msg.file).replace('<div class="attachment', `<div id="msgf-${escHtml(msgId)}" class="attachment`);
    }

    function scrollToBottom() {
        const activeView = getActiveView();
        const feed = document.getElementById("message-feed");
        if (_tailDetached(feed)) {
            state._unreadSeen = true;
            renderMessages();
        }
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
        const slice = allMessages.slice(-RENDER_WINDOW).map(_current).filter(Boolean);
        feed.innerHTML = "";
        state._lastRenderedDate = null;
        state._detachedTail = false;
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
        state._detachedTail = false;
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

    // opts.live: the message just arrived (or was just sent), so its row gets
    // the .msg-enter fade. History loads and re-renders leave it off.
    function renderMessage(msg, opts = {}) {
        const activeView = getActiveView();
        const allMessages = getAllMessages();
        const messageMap = getMessageMap();
        // Skip DOM work for messages that don't belong to the active thread.
        // With no thread open the feed holds the welcome actions, so a thread
        // message has nowhere to go (it used to land under the welcome).
        if (msg.thread_id && msg.thread_id !== activeView?.id) return;
        // Push to allMessages array (virtual scroll buffer)
        const buf = _bufferIds(allMessages);
        const isNewToBuffer = !buf.ids.has(msg.message_id);
        if (isNewToBuffer) {
            allMessages.push(msg);
            buf.ids.add(msg.message_id);
            buf.len = allMessages.length;
        }
        // Carry the client pod-partition timestamp across the server echo, so a later
        // edit/delete/seq addresses the day file the message was actually written to
        // (the echo's server clock can land on a different UTC day).
        const _prevPodTs = messageMap[msg.message_id]?.pod_ts;
        if (_prevPodTs && !msg.pod_ts) msg.pod_ts = _prevPodTs;
        messageMap[msg.message_id] = msg;
        const feed = document.getElementById("message-feed");
        const rendered = !!document.getElementById(`msg-${msg.message_id}`);
        const detached = _tailDetached(feed);
        // Only append a new message (the newest in the buffer), and only while
        // the rendered rows reach the bottom; a reader paged far up gets the
        // scroll-to-bottom count instead.
        if (detached && isNewToBuffer) {
            state._scrollBottomUnread++;
            _updateScrollBottomBtn();
        } else if (!rendered && !detached && (isNewToBuffer || allMessages.length <= RENDER_WINDOW)) {
            // The "No messages yet." hero is added when a thread opens empty;
            // clear it the moment real content arrives, or it floats above the
            // messages forever (same stale-empty-state class as the sidebar CTA).
            feed.querySelector(".empty-state")?.remove();
            const atBottom = isFeedAtBottom(feed);
            // Every message, replies included, goes at the end in arrival
            // (timestamp) order; a reply shows an inline quote of its parent.
            const lastEl = _lastMsgEl(feed);
            const prev = lastEl ? messageMap[lastEl.dataset.messageId] : null;
            const isNew = !!opts.live;
            _renderMessageEl(msg, feed, prev);
            if (isNew) document.getElementById(`msg-${msg.message_id}`)?.classList.add("msg-enter");
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
        const msgExpiresAt = messageExpiresAt(msg.timestamp, currentDisappearMs, getCurrentDisappearSince());
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
            // Fade in the first time this divider is drawn, not on every re-render.
            if (state._unreadEnteredFor !== msgId) {
                state._unreadEnteredFor = msgId;
                unreadEl.classList.add("unread-divider--enter");
            }
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
        if (msgExpiresAt !== null) div.dataset.expiresAt = String(msgExpiresAt);

        const name = msg.from_display_name || (msg.from_webid || "").slice(0, 12) || (msg.from_pub_hex || "").slice(0, 12);
        // Same ten-character tag as the member, contact and DM rows.
        const suffix = didIdTag(msg.from_webid || msg.from_pub_hex || "");
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
            ? `<img src="data:image/png;base64,${b64attr(msg.from_avatar_b64)}" class="avatar avatar--lg" alt="">`
            : `<div class="avatar placeholder avatar--lg" style="background:${avatarColor}">${escHtml(avatarInitial(name))}</div>`;
        const presenceDot = `<div class="avatar-presence ${presenceClass}" title="${escHtml(presenceLabel(presenceData.status))}"></div>`;
        const avatarHtml = `<div class="avatar-wrap is-clickable" data-profile-avatar data-msg-action="profile" data-webid="${escHtml(msg.from_webid || '')}" data-name="${escHtml(name)}">${avatarBase}${presenceDot}</div>`;

        // Render text with Markdown and mention highlighting
        let rawText = msg.snippet || msg.content || "";
        // A DM this device could not open: a localized notice, never the
        // ciphertext (still sealed) or the internal placeholder string.
        const undecryptable = !!msg.e2e || isUndecryptable(rawText);
        if (undecryptable) rawText = "";
        const selfDisplayName = localStorage.getItem("proxion_display_name") || "";
        const mentionsMe = (msg.mentions && selfWebId && msg.mentions.includes(selfWebId)) ||
            (selfDisplayName && rawText.toLowerCase().includes("@" + selfDisplayName.toLowerCase()));
        if (mentionsMe) div.classList.add("mention-highlight");
        // Known names (the message's explicit mentions, room members, ourselves)
        // match whole, so "@Ana María" and other multi-word or non-ASCII names
        // highlight in full.
        const _mentionNames = [selfDisplayName, ...getMemberNames(),
            ...(Array.isArray(msg.mentions) ? msg.mentions.map(w => resolveName(w)) : [])];
        let renderedText = undecryptable
            ? `<span class="msg-undecryptable">${escHtml(t('e2e.undecryptable'))}</span>`
            : highlightMentions(renderMarkdown(rawText), _mentionNames, selfDisplayName);
        // R59G: replace :name: tokens with this room's custom emoji (post-escape,
        // map-driven — unknown names pass through untouched).
        renderedText = applyRoomEmoji(renderedText, getRoomEmoji(msg.thread_id || activeView?.id || ''));

        let fileHtml = "";
        if (msg.file) {
            fileHtml = attachmentHtml(msg.file);
            // A stored room file arrives in history without its bytes: ask the
            // gateway for them (batched; the card fills in on file_data).
            if (msg.file.stored === true && !msg.file.data_b64 && msg.file.unavailable !== true) {
                _requestFile(msgId);
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
        let bodyHtml = "";

        // Inline reply quote: one muted line above the message, click jumps to
        // the parent (scroll-reply). A deleted parent leaves a tombstone in
        // messageMap, which renders as "Original message was deleted".
        if (msg.reply_to_id) {
            const parent = messageMap[msg.reply_to_id];
            const replyIdEsc = escHtml(msg.reply_to_id);
            if (parent) {
                bodyHtml += `<div class="reply-context" data-msg-action="scroll-reply" data-reply-id="${replyIdEsc}">${replyQuoteHtml(parent)}</div>`;
            } else {
                // Parent not in the buffer: fetch it once, fill the quote when it arrives
                bodyHtml += `<div class="reply-context reply-context-loading" data-msg-action="scroll-reply" data-reply-id="${replyIdEsc}" data-reply-target="${replyIdEsc}"><em>${t('msg.loadingReply')}</em></div>`;
                if (!state._requestedReplies.has(msg.reply_to_id)
                        && socket && socket.readyState === WebSocket.OPEN) {
                    state._requestedReplies.add(msg.reply_to_id);
                    socket.send(JSON.stringify({ cmd: "get_message", message_id: msg.reply_to_id }));
                }
            }
        }

        // Round 68: forwarded banner
        if (msg.forwarded) {
            bodyHtml += `<div class="forwarded-banner">${icon('forward', { size: 12 })} ${t('msg.forwardedFrom', { name: escHtml(msg.forwarded_from_name || '') })}</div>`;
        }

        // Header: name + timestamp (first in group only)
        if (!isGrouped) {
            const suffixHtml = suffix ? `<span class="id-suffix">·${escHtml(suffix)}</span>` : "";
            const botBadge = msg.is_bot ? `<span class="bot-badge">BOT</span>` : "";
            const importedBadge = msg.imported ? `<span class="msg-badge-imported">Imported</span>` : "";
            // R11.2.3: unverified shield for DID contacts not yet verified
            const isVerified = !msg.from_webid || msg.from_webid === selfWebId ||
                isPeerVerified(msg.from_webid);
            const shieldHtml = (!isVerified && msg.from_webid && msg.from_webid.startsWith("did:key:"))
                ? `<span title="${t('msg.identityUnverified')}" class="msg-trust-icon text-muted">&#x1F6E1;</span>`
                : "";
            // R107 / #4: an incoming DM or Long Chat room message whose signature
            // did not verify against the author's published identity — surfaced, not
            // blocked. The sender_verified===false flag is set only on the paths that
            // carry a proof (gateway-free DM single + fanout, and pod-read room
            // messages), so it already scopes this to those messages.
            const dmAuthHtml = (msg.sender_verified === false && msg.from_webid && msg.from_webid !== selfWebId)
                ? `<span title="${t('msg.senderUnverified')}" class="msg-trust-icon text-warning">&#x26A0;&#xFE0F;</span>`
                : "";
            // R11.1.3: expiry countdown label
            let expireHtml = "";
            if (msgExpiresAt !== null) {
                const expiresAt = msgExpiresAt;
                expireHtml = `<span class="msg-expire-countdown" title="${t('msg.expires')}">${icon('clock', { size: 12 })} <span class="msg-expire-label">${_expireLabel(expiresAt - Date.now())}</span></span>`;
            }
            bodyHtml += `<div class="msg-header"><span class="msg-sender" style="color:${avatarColor}">${escHtml(name)}${botBadge}${suffixHtml}${shieldHtml}${dmAuthHtml}</span><span class="msg-ts-header">${timeHtml(msg.timestamp, "msg-ts-time")}${importedBadge}${expireHtml}</span></div>`;
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
            // Whole seconds, rounded down like the player and the recording timer,
            // so a 2.6 s clip reads 0:02 everywhere rather than 3s next to 0:02.
            const _durSecs = msg.duration_ms ? Math.max(1, Math.floor(msg.duration_ms / 1000)) : 0;
            const dur = _durSecs ? `<span class="audio-duration">${Math.floor(_durSecs / 60)}:${String(_durSecs % 60).padStart(2, '0')}</span>` : "";
            const _audioLabel = escHtml(t('msg.voiceFrom', { name }) + (_durSecs ? t('msg.voiceDuration', { secs: _durSecs }) : ""));
            bodyHtml += `<div id="msgc-${msgIdEsc}" class="audio-message"><audio controls aria-label="${_audioLabel}" src="data:audio/webm;base64,${b64attr(msg.audio_b64)}"></audio>${dur}${receiptHtml}</div>`;
        } else if (_poll) {
            // R59F: poll card — plain text on the wire, upgraded rendering here.
            // Each option is a vote button; renderReactions fills in the
            // counts from the keycap reactions, so they live-update.
            const rows = _poll.options.map(o =>
                `<button type="button" class="poll-opt" data-msg-action="poll-vote" data-msg-id="${msgIdEsc}" data-emoji="${o.emoji}" aria-pressed="false"><span class="poll-opt-bar" aria-hidden="true"></span><span class="poll-opt-text">${o.emoji} ${escHtml(o.text)}</span><span class="poll-opt-count"></span></button>`).join('');
            bodyHtml += `<div id="msgc-${msgIdEsc}" class="msg-content"><div class="poll-card" dir="auto">
                <div class="poll-q">${escHtml(_poll.question)}</div>${rows}
                <div class="poll-foot"><span class="poll-hint">${t('poll.voteHint')}</span>${editedHtml}${receiptHtml}</div></div></div>`;
        } else if (isFileCaption(msg)) {
            // The card names the file; the "📎 name" caption would repeat it.
            bodyHtml += `<div id="msgc-${msgIdEsc}" class="msg-content msg-content--file"><span class="msg-text" dir="auto"></span>${editedHtml}${receiptHtml}</div>`;
        } else {
            bodyHtml += `<div id="msgc-${msgIdEsc}" class="msg-content"><span class="msg-text" dir="auto">${renderedText}</span>${editedHtml}${receiptHtml}</div>`;
        }

        if (fileHtml) bodyHtml += fileHtml.replace('<div class="attachment', `<div id="msgf-${msgIdEsc}" class="attachment`);
        bodyHtml += `<div id="reactions-${msgIdEsc}" class="reactions"></div>`;

        // Hover action bar
        bodyHtml += `<div class="msg-actions">
            <button data-msg-action="react" data-msg-id="${msgIdEsc}" class="icon-btn" aria-label="${t('msg.react')}" title="${t('msg.react')}">${icon('face-smile')}</button>
            <button data-msg-action="reply" data-msg-id="${msgIdEsc}" class="icon-btn" aria-label="${t('msg.reply')}" title="${t('msg.reply')}">${icon('reply')}</button>
            ${editBtn}
            <button data-msg-action="more" data-msg-id="${msgIdEsc}" class="icon-btn" aria-label="${t('ui.moreActions')}" title="${t('ui.moreActions')}" aria-haspopup="true">${icon('ellipsis-horizontal')}</button>
        </div>`;
        body.innerHTML = bodyHtml;
        // The label names the sender and time; the content is the
        // description, so a screen reader landing on the message (arrow keys
        // in the feed) reads what it says, not just who said it when.
        div.setAttribute("aria-describedby", `msgc-${msgId}` + (fileHtml ? ` msgf-${msgId}` : ""));

        div.appendChild(avatarCol);
        div.appendChild(body);
        div.addEventListener("contextmenu", e => openCtxMenu(e, msgId));
        feed.appendChild(div);
        if (opts.deferReactions) opts.deferReactions.push(msgId);
        else renderReactions(msgId);
    }

    // --- Windowed feed ------------------------------------------------------
    // The DOM holds a contiguous run of the buffer (allMessages). Paging older
    // prepends only the new rows; past MAX_RENDERED rows the far end is
    // dropped and rendered again when the reader scrolls back to it.

    // Build rows for `list` off-document. Reactions are drawn once the rows
    // are in the document (renderReactions looks them up by id).
    function _renderBatch(list, prev) {
        const frag = typeof document.createDocumentFragment === "function"
            ? document.createDocumentFragment() : document.createElement("div");
        const ids = [];
        for (const m of list) {
            const msg = _current(m);
            if (!msg) continue;
            _renderMessageEl(msg, frag, prev, { deferReactions: ids });
            prev = msg;
        }
        return { frag, ids };
    }

    function _insertBatch(feed, batch, ref) {
        const { frag, ids } = batch;
        if (frag.nodeType === 11) feed.insertBefore(frag, ref || null);
        else while (frag.firstElementChild) feed.insertBefore(frag.firstElementChild, ref || null);
        ids.forEach(id => renderReactions(id));
    }

    function _labelOf(el) {
        const m = el && getMessageMap()[el.dataset.messageId];
        return m && m.timestamp ? _dateLabelForTimestamp(m.timestamp) : null;
    }

    // A system note (join, ban ...) belongs under the same date heading as
    // the messages around it: put the heading in first when the day changed.
    function ensureDateDivider(timestamp) {
        const feed = document.getElementById("message-feed");
        if (!feed || !timestamp) return;
        const label = _dateLabelForTimestamp(timestamp);
        if (label === state._lastRenderedDate) return;
        state._lastRenderedDate = label;
        feed.appendChild(_dateDividerEl(label));
    }

    function _dateDividerEl(label) {
        const d = document.createElement("div");
        d.className = "date-divider";
        d.innerHTML = `<span>${escHtml(label)}</span>`;
        return d;
    }

    function _bufferIndexOf(id, fromEnd = false) {
        const am = getAllMessages();
        if (fromEnd) {
            for (let i = am.length - 1; i >= 0; i--) if (am[i].message_id === id) return i;
            return -1;
        }
        return am.findIndex(m => m.message_id === id);
    }

    // Drop rows beyond MAX_RENDERED from the top or the bottom. Never drops
    // the row holding keyboard focus.
    function _trimFar(feed, side) {
        if (!(MAX_RENDERED > 0)) return;
        const els = Array.from(feed.querySelectorAll(".message[data-message-id]"));
        const excess = els.length - MAX_RENDERED;
        if (excess <= 0) return;
        const ae = document.activeElement;
        const focusRow = ae && typeof ae.closest === "function" ? ae.closest(".message") : null;
        const fi = focusRow ? els.indexOf(focusRow) : -1;
        if (side === "bottom") {
            if (fi >= els.length - excess) return;
            let el = _dividerRunStart(els[els.length - excess]);
            while (el) { const nx = el.nextElementSibling; el.remove(); el = nx; }
            state._lastRenderedDate = _labelOf(_lastMsgEl(feed));
            state._detachedTail = true;
            return;
        }
        if (fi >= 0 && fi < excess) return;
        const keep = els[excess];
        const anchor = captureScrollAnchor(feed);
        const fromBottom = feed.scrollHeight - feed.scrollTop;
        const stop = _dividerRunStart(keep);
        while (feed.firstElementChild && feed.firstElementChild !== stop) feed.firstElementChild.remove();
        // The new first row starts a day and a group of its own.
        if (!(stop !== keep && stop.classList.contains("date-divider"))) {
            const label = _labelOf(keep);
            if (label) feed.insertBefore(_dateDividerEl(label), stop);
        }
        if (keep.classList.contains("msg-grouped")) _promoteToGroupHead(keep);
        if (!restoreScrollAnchor(feed, anchor)) feed.scrollTop = feed.scrollHeight - fromBottom;
    }

    // Render up to `count` buffered messages above the first rendered row,
    // keeping the row the reader is on in place. Returns how many it drew.
    function _prependOlder(feed, count) {
        const first = _firstMsgEl(feed);
        if (!first) return 0;
        const idx = _bufferIndexOf(first.dataset.messageId);
        if (idx <= 0) return 0;
        const list = getAllMessages().slice(Math.max(0, idx - count), idx);
        _busy(feed, () => {
            const anchor = captureScrollAnchor(feed);
            const fromBottom = feed.scrollHeight - feed.scrollTop;
            const saved = state._lastRenderedDate;
            state._lastRenderedDate = null;
            const batch = _renderBatch(list, null);
            const lastLabel = state._lastRenderedDate;
            state._lastRenderedDate = saved;
            const run = _dividerRunStart(first);
            _insertBatch(feed, batch, run);
            // Same day across the seam: the old first row's day divider goes.
            if (run !== first && run.classList.contains("date-divider") && lastLabel && lastLabel === _labelOf(first)) {
                run.remove();
            }
            if (!restoreScrollAnchor(feed, anchor)) feed.scrollTop = feed.scrollHeight - fromBottom;
            _trimFar(feed, "bottom");
        });
        return list.length;
    }

    // Render up to `count` buffered messages below the last rendered row
    // (the reader scrolls back down after paging far up).
    function _appendNewer(feed, count) {
        const am = getAllMessages();
        const last = _lastMsgEl(feed);
        const idx = last ? _bufferIndexOf(last.dataset.messageId, true) : -1;
        if (idx < 0) { renderMessages(); return; }
        const list = am.slice(idx + 1, idx + 1 + count);
        _busy(feed, () => {
            _insertBatch(feed, _renderBatch(list, getMessageMap()[last.dataset.messageId] || null), null);
            if (idx + 1 + count >= am.length) state._detachedTail = false;
            _trimFar(feed, "top");
        });
    }

    // Make sure a buffered message has a row (reply / pin / search jumps to
    // a message that was paged out or never rendered). Returns the row, or
    // null when the message is not in the buffer.
    function revealMessage(msgId) {
        const existing = document.getElementById(`msg-${msgId}`);
        if (existing) return existing;
        const am = getAllMessages();
        const idx = _bufferIndexOf(msgId);
        const feed = document.getElementById("message-feed");
        if (idx < 0 || !feed) return null;
        const from = Math.max(0, Math.min(idx - Math.floor(RENDER_WINDOW / 2), am.length - RENDER_WINDOW));
        const to = Math.min(am.length, from + RENDER_WINDOW);
        _busy(feed, () => {
            feed.innerHTML = "";
            state._lastRenderedDate = null;
            _renderThreaded(am.slice(from, to).map(_current).filter(Boolean), feed);
            state._detachedTail = to < am.length;
            state._pinnedToBottom = false;
            if (state._detachedTail) _updateScrollBottomBtn();
        });
        return document.getElementById(`msg-${msgId}`);
    }

    // C3: merge a batch of older messages (e.g. a federated /room-history page)
    // into the buffer: dedupe, keep chronological order, draw only the new
    // rows above the rendered ones and keep the message the reader was on
    // anchored in place. Returns the number of new messages actually merged.
    function mergeOlderHistory(olderMsgs) {
        const am = getAllMessages();
        const mm = getMessageMap();
        const seen = _bufferIds(am).ids;
        const older = (olderMsgs || []).filter(m => m && m.message_id && !seen.has(m.message_id));
        if (!older.length) return 0;
        const feed = document.getElementById("message-feed");
        const firstEl = feed ? _firstMsgEl(feed) : null;
        older.forEach(m => { mm[m.message_id] = m; am.push(m); });
        am.sort((a, b) => (a.timestamp || "").localeCompare(b.timestamp || ""));
        if (!feed) return older.length;
        if (firstEl) {
            const idx = _bufferIndexOf(firstEl.dataset.messageId);
            const newIds = new Set(older.map(m => m.message_id));
            let landedInside = false;
            for (let i = Math.max(idx, 0); i < am.length && !landedInside; i++) landedInside = newIds.has(am[i].message_id);
            if (idx >= 0 && !landedInside) {
                _prependOlder(feed, older.length);
                return older.length;
            }
            // Some of the page sorts in among the rendered rows: redraw the
            // rendered range plus the new rows.
            const lastEl = _lastMsgEl(feed);
            const end = lastEl ? _bufferIndexOf(lastEl.dataset.messageId, true) + 1 : am.length;
            let start = Math.max(0, idx < 0 ? 0 : idx);
            for (let i = 0; i < am.length; i++) if (newIds.has(am[i].message_id)) { start = Math.min(start, i); break; }
            _busy(feed, () => {
                rerenderKeepingAnchor(am.slice(start, Math.max(end, start)).map(_current).filter(Boolean), feed);
                if (end < am.length) {
                    state._detachedTail = true;
                    state._lastRenderedDate = _labelOf(_lastMsgEl(feed));
                }
                _trimFar(feed, "bottom");
            });
            return older.length;
        }
        rerenderKeepingAnchor(am.slice(-older.length).map(_current).filter(Boolean), feed);
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
            const detached = _tailDetached(feed);
            const atBottom = isFeedAtBottom(feed);
            state._pinnedToBottom = atBottom && !detached;
            // Hide scroll-to-bottom btn when user scrolls to bottom
            if (state._pinnedToBottom) {
                state._scrollBottomUnread = 0;
                state._unreadSeen = true;
                document.getElementById("scroll-bottom-btn").style.display = "none";
            }
            // Paged far up and back down to the last rendered row: render
            // the next newer rows from the buffer.
            if (detached && atBottom) { _appendNewer(feed, SCROLL_BATCH); return; }
            if (feed.scrollTop !== 0) return;
            // First expand from the in-memory buffer, but only while it holds
            // older messages that are not rendered yet. Once the oldest
            // buffered message is on screen, fall through and ask for more.
            const first = _firstMsgEl(feed);
            const firstIdx = first ? _bufferIndexOf(first.dataset.messageId) : -1;
            if (firstIdx > 0) { _prependOlder(feed, SCROLL_BATCH); return; }
            const rendered = feed.querySelectorAll(".message").length;
            if (firstIdx < 0 && allMessages.length > rendered) {
                rerenderKeepingAnchor(allMessages.slice(-Math.min(rendered + SCROLL_BATCH, allMessages.length)).map(_current).filter(Boolean), feed);
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
                    fetch(gatewayUrl(`/room-history/${encodeURIComponent(activeView.id)}?code=${encodeURIComponent(_code)}&before=${encodeURIComponent(_oldest.timestamp)}&limit=${SCROLL_BATCH}`))
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
        _renderMessageEl, _dateLabelForTimestamp, ensureDateDivider,
        markUnread, setUnreadBefore, scrollToUnread, clearUnreadDivider, resetUnread,
        removeMessage, fillAttachment,
        mergeOlderHistory, rerenderKeepingAnchor, revealMessage, attach, state,
    };
}
