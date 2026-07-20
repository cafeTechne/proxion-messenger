// blocks.js — R65: user blocking. The gateway already ENFORCES blocks (it drops
// inbound relays from a blocked WebID); this module is the missing client
// FEATURE: block/unblock actions, the block-state set (hydrated from the
// gateway's authoritative list_blocks), a settings manage-list, and opt-in
// mirroring to the pod so blocks follow you across devices.
//
// Blocking is account-level (you block a person everywhere), distinct from a
// room ban. It lives on the profile card, where you act on a specific person.

import { t } from './i18n.js';
import { podWriteBlocks } from './pod.js';

export function createBlocks({ getSocket, showToast, onAfterChange }) {
    const blocked = new Set();

    function isBlocked(webid) { return !!webid && blocked.has(webid); }

    function _send(cmd, webid) {
        const s = getSocket();
        if (s?.readyState === WebSocket.OPEN) s.send(JSON.stringify({ cmd, webid }));
    }

    function block(webid) {
        if (!webid || blocked.has(webid)) return;
        blocked.add(webid);
        _send('block', webid);
        podWriteBlocks([...blocked]).catch(() => {});   // opt-in (no-op unless synced)
        showToast?.(t('blocks.blocked'));
        onAfterChange?.();
    }

    function unblock(webid) {
        if (!webid || !blocked.has(webid)) return;
        blocked.delete(webid);
        _send('unblock', webid);
        podWriteBlocks([...blocked]).catch(() => {});
        showToast?.(t('blocks.unblocked'));
        onAfterChange?.();
    }

    function toggle(webid) {
        if (isBlocked(webid)) unblock(webid); else block(webid);
    }

    // The gateway's list_blocks response is authoritative for this device.
    function handleBlocksEvent(webids) {
        blocked.clear();
        for (const w of webids || []) if (typeof w === 'string') blocked.add(w);
        onAfterChange?.();
    }

    function requestBlocks() {
        const s = getSocket();
        if (s?.readyState === WebSocket.OPEN) s.send(JSON.stringify({ cmd: 'list_blocks' }));
    }

    // Pod hydrate: block anything the pod has that this device doesn't. We do
    // NOT auto-unblock local-only blocks (keeping a block is the safe default;
    // removals propagate through explicit unblock).
    function reconcileFromPod(podWebids) {
        if (!Array.isArray(podWebids)) return;
        for (const w of podWebids) if (typeof w === 'string' && !blocked.has(w)) block(w);
    }

    function renderBlockedList() {
        const list = document.getElementById('settings-blocked-list');
        if (!list) return;
        list.innerHTML = '';
        if (!blocked.size) {
            const p = document.createElement('p');
            p.style.cssText = 'color:var(--text-secondary);font-size:0.85em;margin:4px 0;';
            p.textContent = t('blocks.none');
            list.appendChild(p);
            return;
        }
        for (const webid of blocked) {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid var(--slate-700);';
            const label = document.createElement('code');
            label.style.cssText = 'flex:1;font-size:0.78em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
            label.textContent = webid;
            const btn = document.createElement('button');
            btn.textContent = t('blocks.unblock');
            btn.style.cssText = 'background:transparent;border:1px solid var(--slate-600);color:var(--slate-50);padding:3px 10px;border-radius:4px;cursor:pointer;font-size:0.78em;flex-shrink:0;';
            btn.addEventListener('click', () => { unblock(webid); renderBlockedList(); });
            row.appendChild(label);
            row.appendChild(btn);
            list.appendChild(row);
        }
    }

    // R65: push the current block set to the pod (on enabling sync).
    function pushToPod() { podWriteBlocks([...blocked]).catch(() => {}); }

    return {
        isBlocked, block, unblock, toggle, handleBlocksEvent, requestBlocks,
        reconcileFromPod, renderBlockedList, pushToPod,
    };
}
