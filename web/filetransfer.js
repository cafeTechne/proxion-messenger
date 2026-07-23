// Chunked large-file transfer (R39), extracted from main.js (R40).
// Dependency-injection module: main.js calls createFileTransfer(deps) and wires
// the returned handlers into the WS dispatch + file picker. State and helpers
// live here; cross-cutting concerns (sendCmd, showToast, renderMessage, the
// current view) are injected so this module stays decoupled from main.js.

import { t } from './i18n.js';
import { u8ToB64, b64ToU8 } from './util.js';

const CHUNK_BYTES = 64 * 1024;
const MAX_FILE_BYTES = 25 * 1024 * 1024;

/**
 * @param {object} deps
 * @param {(cmd:string, payload:object)=>void} deps.sendCmd
 * @param {(msg:string, kind?:string)=>void} deps.showToast
 * @param {(msg:object)=>void} deps.renderMessage
 * @param {()=>object|null} deps.getActiveView
 */
import { announce } from './a11y.js';

export function createFileTransfer({ sendCmd, showToast, renderMessage, getActiveView }) {
    const _outgoingFiles = {};   // file_id -> {resolve, reject}
    const _incomingFiles = {};   // file_id -> {meta, chunks[], received, total, fromWebid}

    function _showTransferProgress(fileId, name, pct, verb) {
        let el = document.getElementById("xfer-" + fileId);
        if (!el) {
            el = document.createElement("div");
            el.id = "xfer-" + fileId;
            // progressbar semantics (aria-valuenow updated below). NOT aria-live —
            // per-% updates would flood a screen reader; instead announce() below
            // speaks the state only at quarter marks.
            if (el.setAttribute) {   // real DOM only (vitest stubs createElement)
                el.setAttribute("role", "progressbar");
                el.setAttribute("aria-label", `${verb} ${name}`);
                el.setAttribute("aria-valuemin", "0");
                el.setAttribute("aria-valuemax", "100");
            }
            el.style.cssText = "position:fixed;bottom:8px;right:8px;z-index:1500;background:#1e293b;color:#f1f5f9;padding:6px 12px;border-radius:6px;font-size:0.8em;box-shadow:0 2px 8px rgba(0,0,0,.4);";
            document.body.appendChild(el);
        }
        if (el.setAttribute) el.setAttribute("aria-valuenow", String(pct));
        el.textContent = `${verb} ${name} — ${pct}%`;   // visible: smooth updates
        const _step = Math.floor(pct / 25);
        if (el._lastStep !== _step) { el._lastStep = _step; announce(`${verb} ${name}, ${pct} percent`); }
    }
    function _clearTransferProgress(fileId) {
        const el = document.getElementById("xfer-" + fileId);
        if (el) el.remove();
    }

    async function sendFileChunked(file, toWebid) {
        const fileId = (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36)).replace(/-/g, "").slice(0, 16);
        const buf = new Uint8Array(await file.arrayBuffer());
        const total = Math.ceil(buf.length / CHUNK_BYTES) || 1;
        const acceptP = new Promise((resolve, reject) => {
            _outgoingFiles[fileId] = { resolve, reject };
            setTimeout(() => reject(new Error("accept_timeout")), 30000);
        });
        sendCmd("file_offer", {
            to_webid: toWebid, file_id: fileId, filename: file.name,
            mime_type: file.type || "application/octet-stream",
            size_bytes: buf.length, total_chunks: total,
        });
        showToast(`Offering ${file.name}…`);
        try { await acceptP; } catch (e) { delete _outgoingFiles[fileId]; _clearTransferProgress(fileId); showToast(t('file.notAccepted')); return; }
        for (let seq = 0; seq < total; seq++) {
            const slice = buf.subarray(seq * CHUNK_BYTES, (seq + 1) * CHUNK_BYTES);
            sendCmd("file_chunk", { to_webid: toWebid, file_id: fileId, seq, data: u8ToB64(slice) });
            _showTransferProgress(fileId, file.name, Math.round(((seq + 1) / total) * 100), "Sending");
            if (seq % 8 === 7) await new Promise(r => setTimeout(r, 0)); // yield to UI
        }
        sendCmd("file_complete", { to_webid: toWebid, file_id: fileId });
        _clearTransferProgress(fileId);
        delete _outgoingFiles[fileId];
        showToast(`Sent ${file.name}`);
    }

    function handleFileOffer(event) {
        if (event.size_bytes > MAX_FILE_BYTES) {
            sendCmd("file_reject", { to_webid: event.from_webid, file_id: event.file_id, reason: "too_large" });
            return;
        }
        // total_chunks is attacker-controlled — reject nonsensical counts before
        // allocating an array from it (new Array(huge) / mismatched size).
        const _maxChunks = Math.ceil(MAX_FILE_BYTES / CHUNK_BYTES) + 1;
        const _tc = event.total_chunks;
        if (!Number.isInteger(_tc) || _tc <= 0 || _tc > _maxChunks) {
            sendCmd("file_reject", { to_webid: event.from_webid, file_id: event.file_id, reason: "bad_offer" });
            return;
        }
        _incomingFiles[event.file_id] = {
            meta: { filename: event.filename, mime_type: event.mime_type, size: event.size_bytes },
            chunks: new Array(event.total_chunks), received: 0, total: event.total_chunks,
            fromWebid: event.from_webid,
        };
        sendCmd("file_accept", { to_webid: event.from_webid, file_id: event.file_id });
        showToast(`Receiving ${event.filename}…`);
    }
    function handleFileChunk(event) {
        const rec = _incomingFiles[event.file_id];
        if (!rec) return;
        // seq and data are attacker-controlled. seq MUST be an integer in
        // [0, total): an out-of-range or non-integer seq would write outside the
        // real chunk indices yet still bump `received`, letting the completeness
        // check in handleFileComplete pass while chunks[0..total-1] stay empty.
        // That is exactly the silently-corrupt-file case that check exists to
        // stop, so the guarantee only holds if every counted seq is a valid index.
        const seq = event.seq;
        if (!Number.isInteger(seq) || seq < 0 || seq >= rec.total) return;
        if (typeof event.data !== "string") return;
        if (rec.chunks[seq] === undefined) {
            rec.chunks[seq] = event.data;
            rec.received++;
            _showTransferProgress(event.file_id, rec.meta.filename, Math.round((rec.received / rec.total) * 100), "Receiving");
        }
    }
    function handleFileComplete(event) {
        const rec = _incomingFiles[event.file_id];
        if (!rec) return;
        _clearTransferProgress(event.file_id);
        // Verify every chunk actually arrived. Without this, a dropped or
        // never-delivered chunk is silently filled with empty bytes below,
        // producing a corrupt file the user can't tell apart from a good one.
        if (rec.received < rec.total) {
            delete _incomingFiles[event.file_id];
            showToast(t('file.transferFailed', { filename: rec.meta.filename, received: rec.received, total: rec.total }), "error");
            return;
        }
        let totalLen = 0;
        const parts = [];
        for (let i = 0; i < rec.total; i++) { const u8 = b64ToU8(rec.chunks[i] || ""); parts.push(u8); totalLen += u8.length; }
        const full = new Uint8Array(totalLen);
        let off = 0;
        for (const p of parts) { full.set(p, off); off += p.length; }
        const dataB64 = u8ToB64(full);
        const fromWebid = rec.fromWebid;
        delete _incomingFiles[event.file_id];
        const av = getActiveView();
        const threadId = (av && av.peerWebid === fromWebid) ? av.id : fromWebid;
        renderMessage({
            type: "message", source: "relay", thread_id: threadId,
            from_webid: fromWebid, from_display_name: (fromWebid || "").slice(0, 12),
            content: `📎 ${rec.meta.filename}`, timestamp: new Date().toISOString(),
            message_id: "file-" + event.file_id, local: true,
            file: { filename: rec.meta.filename, mime_type: rec.meta.mime_type, size: rec.meta.size, data_b64: dataB64 },
        });
    }
    function handleFileAccept(event) {
        const rec = _outgoingFiles[event.file_id];
        if (rec && rec.resolve) rec.resolve();
    }
    function handleFileReject(event) {
        const rec = _outgoingFiles[event.file_id];
        if (rec && rec.reject) rec.reject(new Error(event.reason || "rejected"));
        _clearTransferProgress(event.file_id);
        showToast(t('file.declined'));
    }
    function handleFileUnreachable(event) {
        const rec = _outgoingFiles[event.file_id];
        if (rec && rec.reject) rec.reject(new Error("unreachable"));
        _clearTransferProgress(event.file_id);
        showToast(t('file.recipientOffline'));
    }

    return {
        MAX_FILE_BYTES,
        sendFileChunked,
        handleFileOffer, handleFileChunk, handleFileComplete,
        handleFileAccept, handleFileReject, handleFileUnreachable,
    };
}
