// solidchat.js — cross-app conversations over Solid Long Chat.
//
// A "Solid conversation" is a shared Long Chat that lives in ONE pod and that
// several people, on potentially different apps (Proxion, SolidOS, POD-CHAT),
// read and post to. This is distinct from a Proxion E2E DM and from a gateway
// room:
//   * it is PLAINTEXT (the other app cannot do the Double Ratchet), protected by
//     pod ACLs, not encrypted. The UI must label it as such.
//   * it reaches anyone with a pod/WebID, in any Solid chat app.
//
// This module is the model + operations (host / join / send / load / list). It
// holds the list of conversations the user takes part in (in localStorage) and
// drives the verified pod primitives. Live update and UI live elsewhere.

import { solidSession, podStorageRoot } from './auth.js';
import {
    podWriteChatMessageAt, podReadChatRecentAt, podGrantChatParticipants,
} from './pod.js';
import { chatRootUrl } from './longchat.js';

const STORE_KEY = 'proxion_solid_conversations';

function _uuid() {
    try {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    } catch { /* fall through */ }
    return 'c-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function _myWebId() {
    return (solidSession && solidSession.info && solidSession.info.webId) || '';
}

/** A container URL must be an absolute http(s) URL ending in '/'. */
export function isValidChatContainer(url) {
    if (typeof url !== 'string') return false;
    if (!/^https?:\/\//.test(url)) return false;
    if (!url.endsWith('/')) return false;
    // No fragment/query, no whitespace, no obvious traversal.
    if (/[\s?#]/.test(url) || url.includes('..')) return false;
    try { new URL(url); return true; } catch { return false; }
}

export function createSolidChat({ showToast = () => {}, onChange = () => {} } = {}) {

    function _load() {
        try {
            const raw = localStorage.getItem(STORE_KEY);
            const arr = raw ? JSON.parse(raw) : [];
            return Array.isArray(arr) ? arr.filter(c => c && isValidChatContainer(c.id)) : [];
        } catch { return []; }
    }
    function _save(list) {
        try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); } catch { /* quota */ }
        onChange(list);
    }
    function _upsert(conv) {
        const list = _load();
        const i = list.findIndex(c => c.id === conv.id);
        if (i >= 0) list[i] = { ...list[i], ...conv };
        else list.push(conv);
        _save(list);
        return conv;
    }

    /** All conversations the user hosts or has joined, most-recent first. */
    function listConversations() {
        return _load().slice().sort((a, b) => String(b.lastAt || '').localeCompare(String(a.lastAt || '')));
    }

    function getConversation(id) {
        return _load().find(c => c.id === id) || null;
    }

    /**
     * Create a conversation in OUR pod and grant the given participants write
     * access so they can post. Returns the conversation ref (its `id` is the
     * shareable container URL) or null on failure.
     */
    async function hostConversation({ title = 'Conversation', participantWebIds = [] } = {}) {
        const root = podStorageRoot();
        const me = _myWebId();
        if (!root || !me) { showToast('Connect a pod to host a conversation'); return null; }
        const roomId = 'sc-' + _uuid();
        const container = chatRootUrl(root, roomId);
        const participants = [...new Set(participantWebIds)].filter(w => w && w !== me);

        // Seed the channel (index.ttl) by writing nothing yet is not possible with
        // a PATCH-only primitive, so grant access first, then the first send
        // creates the index + day file. Granting also creates the container.
        const granted = await podGrantChatParticipants(container, me, participants);
        if (!granted) { showToast('Could not set up the conversation'); return null; }

        return _upsert({
            id: container, title: String(title).slice(0, 200), host: me,
            participants, role: 'host', createdAt: new Date().toISOString(), lastAt: null,
        });
    }

    /**
     * Record a conversation hosted elsewhere (a URL someone shared, or a chat we
     * were granted access to). Confirms we can actually read it before adding, so
     * a bad or forbidden URL fails loudly rather than sitting broken in the list.
     */
    async function joinConversation(containerUrl, { title = 'Conversation' } = {}) {
        if (!isValidChatContainer(containerUrl)) { showToast('That is not a valid conversation link'); return null; }
        if (!_myWebId()) { showToast('Connect a pod to join a conversation'); return null; }
        // A read attempt doubles as an access check.
        try {
            await podReadChatRecentAt(containerUrl, 1, containerUrl);
        } catch {
            showToast('Could not open that conversation (no access?)');
            return null;
        }
        return _upsert({
            id: containerUrl, title: String(title).slice(0, 200), host: null,
            participants: [], role: 'participant', createdAt: new Date().toISOString(), lastAt: null,
        });
    }

    /** Remove a conversation from OUR list. Does not touch the pod data. */
    function leaveConversation(id) {
        _save(_load().filter(c => c.id !== id));
    }

    /** Post a message as ourselves into a conversation. */
    async function sendMessage(id, text) {
        const conv = getConversation(id);
        const me = _myWebId();
        if (!conv || !me) return false;
        const body = String(text == null ? '' : text).trim();
        if (!body) return false;
        const ts = new Date().toISOString();
        const ok = await podWriteChatMessageAt(id, 'm-' + _uuid(), {
            content: body, from_webid: me, timestamp: ts, room_name: conv.title,
        });
        if (ok) _upsert({ id, lastAt: ts });
        else showToast('Message not sent');
        return ok;
    }

    /** Load recent messages for a conversation (oldest first). */
    async function loadConversation(id, days = 7) {
        if (!getConversation(id)) return [];
        return podReadChatRecentAt(id, days, id);
    }

    /**
     * Watch a conversation for new messages and call `onMessages(fresh)` with any
     * that appear. Polling based: a shared chat lives in another pod, and polling
     * the current UTC day is the portable way to notice new posts on any server.
     * (Solid Notifications / Live Update is a later enhancement over the same
     * shape.) Returns an unsubscribe function; safe to call more than once.
     */
    function subscribeConversation(id, onMessages, { intervalMs = 5000, days = 1 } = {}) {
        const seen = new Set();
        let stopped = false;
        let timer = null;
        async function tick() {
            if (stopped) return;
            let msgs = [];
            try { msgs = await loadConversation(id, days); } catch { /* keep polling */ }
            const fresh = msgs.filter(m => m.message_id && !seen.has(m.message_id));
            fresh.forEach(m => seen.add(m.message_id));
            if (fresh.length && !stopped) onMessages(fresh);
            if (!stopped) timer = setTimeout(tick, intervalMs);
        }
        tick();
        return () => { stopped = true; if (timer) clearTimeout(timer); };
    }

    return {
        listConversations, getConversation,
        hostConversation, joinConversation, leaveConversation,
        sendMessage, loadConversation, subscribeConversation,
        _STORE_KEY: STORE_KEY,
    };
}
