import { createAssetInventory, INVENTORY_PAGE } from './asset-inventory.js';
import { escHtml, formatFileSize } from './util.js';
import { t } from './i18n.js';

export function createAssetInventoryUi({ getContext, getRoomName = id => id, getReferences = () => [], doc = document }) {
    let generation = 0, displayed = null, roomId = '', path = '', offset = 0, next = null, rootsPartial = false;
    let pending = null;
    const el = id => doc.getElementById('asset-inventory-' + id);
    function clear() {
        pending?.abort(); pending = null;
        generation++; displayed = null; roomId = ''; path = ''; offset = 0; next = null; rootsPartial = false;
        if (el('rooms')) el('rooms').replaceChildren();
        if (el('results')) el('results').replaceChildren();
        if (el('status')) el('status').textContent = '';
        if (el('browser')) el('browser').hidden = true;
    }
    function sync() {
        const ctx = getContext(), scope = ctx ? ctx.actor + '\n' + ctx.root : null;
        if (scope !== displayed) clear();
        displayed = scope;
        if (el('refresh')) el('refresh').disabled = !ctx;
    }
    function status(text) { if (el('status')) el('status').textContent = text; }
    function context() {
        const ctx = getContext(); if (!ctx) { sync(); status(t('inventory.notConnected')); return null; }
        pending?.abort(); pending = new AbortController();
        const token = ++generation;
        return { ...ctx, signal: pending.signal, current: () => token === generation && ctx.current() };
    }
    async function browse() {
        const ctx = context(); if (!ctx || !roomId) return;
        const target = roomId, targetPath = path;
        status(t('inventory.loading')); el('results').replaceChildren();
        el('previous').disabled = true; el('next').disabled = true; el('up').disabled = true;
        const result = await createAssetInventory(ctx).browse(target, { path: targetPath, offset, references: getReferences(target) });
        if (!ctx.current()) return;
        if (!result.ok) { status(t('inventory.failed')); return; }
        next = result.next;
        el('path').textContent = target + '/' + targetPath;
        el('up').disabled = !path; el('previous').disabled = offset === 0; el('next').disabled = next === null;
        status(result.partial || rootsPartial ? t('inventory.partial') : (result.rows.length ? t('inventory.reviewHint') : t('inventory.empty')));
        if (!result.rows.length) return;
        el('results').innerHTML = '<table class="asset-inventory-table"><thead><tr>'
            + [t('inventory.name'), t('inventory.kind'), t('inventory.size'), t('inventory.reference')].map(label => `<th scope="col">${escHtml(label)}</th>`).join('')
            + '</tr></thead><tbody>' + result.rows.map(row => '<tr><td>'
                + (row.kind === 'folder' ? `<button type="button" class="btn btn--sm" data-asset-inventory-path="${escHtml(row.path)}">${escHtml(row.name)}</button>` : escHtml(row.name))
                + `</td><td>${escHtml(({ chunk: t('inventory.kind.chunk'), folder: t('inventory.kind.folder'), voice: t('inventory.kind.voice'), legacy: t('inventory.kind.legacy') })[row.kind])}</td><td>${escHtml(row.kind === 'folder' ? '—' : row.size === null ? t('inventory.unknownSize') : formatFileSize(row.size))}</td>`
                + `<td>${escHtml(row.kind === 'folder' ? '—' : row.available === false ? t('inventory.unavailable') : row.referenced ? t('inventory.referenced') : t('inventory.unknownReference'))}</td></tr>`).join('')
            + '</tbody></table>';
    }
    async function refresh() {
        clear(); sync();
        const ctx = context(); if (!ctx) return;
        status(t('inventory.loading'));
        const result = await createAssetInventory(ctx).rooms();
        if (!ctx.current()) return;
        if (!result.ok) { status(t('inventory.failed')); return; }
        rootsPartial = result.partial;
        el('rooms').innerHTML = result.rooms.map(id => {
            const name = getRoomName(id);
            return `<option value="${escHtml(id)}">${escHtml(name === id ? id : `${name} (${id})`)}</option>`;
        }).join('');
        if (!result.rooms.length) { status(result.partial ? t('inventory.partial') : t('inventory.emptyRooms')); return; }
        el('browser').hidden = false; roomId = result.rooms[0]; el('rooms').value = roomId;
        await browse();
    }
    el('refresh')?.addEventListener('click', refresh);
    el('rooms')?.addEventListener('change', () => { roomId = el('rooms').value; path = ''; offset = 0; browse(); });
    el('results')?.addEventListener('click', event => {
        const button = event.target.closest('[data-asset-inventory-path]');
        if (button) { path = button.dataset.assetInventoryPath; offset = 0; browse(); }
    });
    el('up')?.addEventListener('click', () => { path = path === 'files/' ? '' : 'files/'; offset = 0; browse(); });
    el('previous')?.addEventListener('click', () => { offset = Math.max(0, offset - INVENTORY_PAGE); browse(); });
    el('next')?.addEventListener('click', () => { if (next !== null) { offset = next; browse(); } });
    return { refresh, browse, reset: clear, sync };
}
