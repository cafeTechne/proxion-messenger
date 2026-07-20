// room-emoji.js — R59G: custom room emoji, the sovereign take on Discord's
// signature feature. Small images live in the room's state on the gateway
// (federated to member gateways as signed deltas); the client keeps a
// per-room name→image map, renders `:name:` tokens inline, and gives
// admins a management modal (upload / list / delete).
//
// applyRoomEmoji is pure and operates on ALREADY-ESCAPED html — the server
// enforces ^[a-z0-9_]{2,32}$ names and image magic bytes, and we only ever
// emit <img> tags built from the map here, so the token replacement cannot
// become an injection path.

import { t } from './i18n.js';
import { downscaleImage } from './media-resize.js';

export const EMOJI_NAME_RE = /^[a-z0-9_]{2,32}$/;
const EMOJI_MAX_BYTES = 64 * 1024;

// roomId → { name: {mime, data_b64} }
const _map = {};

export function setRoomEmoji(roomId, list) {
    const byName = {};
    for (const e of list || []) {
        if (EMOJI_NAME_RE.test(e.name || '')) byName[e.name] = { mime: e.mime, data_b64: e.data_b64 };
    }
    _map[roomId] = byName;
}

export function getRoomEmoji(roomId) {
    return _map[roomId] || {};
}

// escapedHtml + a room's emoji map → html with :name: tokens replaced by
// <img class="custom-emoji">. Unknown names pass through untouched.
export function applyRoomEmoji(escapedHtml, emojiMap) {
    if (!escapedHtml || !emojiMap || !Object.keys(emojiMap).length) return escapedHtml;
    return escapedHtml.replace(/:([a-z0-9_]{2,32}):/g, (m, name) => {
        const e = emojiMap[name];
        if (!e) return m;
        return `<img class="custom-emoji" src="data:${e.mime};base64,${e.data_b64}" alt=":${name}:" title=":${name}:">`;
    });
}

export function createRoomEmoji({ getSocket, getActiveView, showToast, showPromptModal }) {

    function requestList(roomId) {
        const socket = getSocket();
        if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ cmd: 'list_room_emoji', room_id: roomId }));
        }
    }

    // WS "room_emoji" event → update map (+ modal list if open).
    function handleRoomEmojiEvent(data) {
        setRoomEmoji(data.room_id, data.emoji);
        if (document.getElementById('room-emoji-modal')?.style.display === 'flex') {
            _renderList(data.room_id);
        }
    }

    let _modalRoomId = null;

    function openManageModal(roomId) {
        _modalRoomId = roomId;
        const modal = document.getElementById('room-emoji-modal');
        if (!modal) return;
        modal.style.display = 'flex';
        requestList(roomId);
        _renderList(roomId);
    }

    function _closeModal() {
        const modal = document.getElementById('room-emoji-modal');
        if (modal) modal.style.display = 'none';
        _modalRoomId = null;
    }

    function _renderList(roomId) {
        const list = document.getElementById('room-emoji-list');
        if (!list) return;
        const emoji = getRoomEmoji(roomId);
        list.innerHTML = '';
        const names = Object.keys(emoji).sort();
        if (!names.length) {
            const p = document.createElement('p');
            p.style.cssText = 'color:var(--text-secondary);font-size:0.85em;margin:4px 0;';
            p.textContent = t('roomEmoji.none');
            list.appendChild(p);
            return;
        }
        for (const name of names) {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid var(--slate-700);';
            const img = document.createElement('img');
            img.className = 'custom-emoji';
            img.src = `data:${emoji[name].mime};base64,${emoji[name].data_b64}`;
            img.alt = '';
            const label = document.createElement('code');
            label.style.cssText = 'flex:1;font-size:0.85em;';
            label.textContent = `:${name}:`;
            const del = document.createElement('button');
            del.textContent = '×';
            del.setAttribute('aria-label', t('roomEmoji.removeNamed', { name }));
            del.style.cssText = 'background:transparent;border:none;color:var(--color-danger-soft,#f87171);cursor:pointer;font-size:1em;min-width:24px;min-height:24px;';
            del.addEventListener('click', () => {
                getSocket()?.send(JSON.stringify({ cmd: 'remove_room_emoji', room_id: roomId, name }));
            });
            row.appendChild(img);
            row.appendChild(label);
            row.appendChild(del);
            list.appendChild(row);
        }
    }

    async function _uploadEmoji(file) {
        if (!_modalRoomId || !file) return;
        const rawName = (file.name || '').replace(/\.[a-z0-9]+$/i, '')
            .toLowerCase().replace(/[^a-z0-9_]/g, '_').slice(0, 32);
        const name = await showPromptModal(t('roomEmoji.namePrompt'), { placeholder: rawName });
        const finalName = (name || rawName).toLowerCase().trim();
        if (!EMOJI_NAME_RE.test(finalName)) { showToast(t('roomEmoji.badName')); return; }

        let out = file;
        const type = (file.type || '').toLowerCase();
        const okAsIs = file.size <= EMOJI_MAX_BYTES && ['image/png', 'image/webp', 'image/gif'].includes(type);
        if (!okAsIs) {
            if (type === 'image/gif') { showToast(t('roomEmoji.gifTooLarge')); return; }
            try {
                out = await downscaleImage(file, { maxBytes: 60 * 1024, maxDim: 128 });
            } catch (_) {
                showToast(t('roomEmoji.badImage'));
                return;
            }
        }
        const b64 = await new Promise((res, rej) => {
            const r = new FileReader();
            r.onload = () => res(r.result.split(',')[1]);
            r.onerror = rej;
            r.readAsDataURL(out);
        });
        getSocket()?.send(JSON.stringify({
            cmd: 'add_room_emoji', room_id: _modalRoomId,
            name: finalName, mime: out.type, data_b64: b64,
        }));
    }

    function wireRoomEmoji() {
        document.getElementById('room-emoji-close')?.addEventListener('click', _closeModal);
        document.getElementById('room-emoji-upload-btn')?.addEventListener('click', () => {
            document.getElementById('room-emoji-upload-input')?.click();
        });
        document.getElementById('room-emoji-upload-input')?.addEventListener('change', async (e) => {
            const file = e.target.files?.[0];
            if (file) await _uploadEmoji(file);
            e.target.value = '';
        });
    }

    return { requestList, handleRoomEmojiEvent, openManageModal, wireRoomEmoji };
}
