// Chunked large-file transfer (R39), extracted from main.js (R40).
// Dependency-injection module: main.js calls createFileTransfer(deps) and wires
// the returned handlers into the WS dispatch + file picker. State and helpers
// live here; cross-cutting concerns (sendCmd, showToast, renderMessage, the
// current view) are injected so this module stays decoupled from main.js.

import { t } from './i18n.js';
import { u8ToB64, b64ToU8, dmFallbackName } from './util.js';
import { FILE_TAG_BYTES, SEALED_FILE_NAME, SEALED_FILE_MIME, openFileBytes } from './e2e-secondary.js';

// 64 KiB of file is ~88 KB of base64, which leaves ~40 KB of headroom under
// the gateway's 128 KiB /relay body cap for the signed envelope.
const CHUNK_BYTES = 64 * 1024;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
// Send side flow control. Each chunk is acked by our gateway once it reached
// the recipient (or their gateway); at most CHUNK_WINDOW stay unacked, so
// chunks are paced by the real relay instead of flooding it. The transfer only
// counts as sent once the receiver confirms it reassembled the whole file.
const CHUNK_WINDOW = 8;
const CHUNK_ACK_TIMEOUT_MS = 45 * 1000;
const RECEIPT_TIMEOUT_MS = 60 * 1000;
// Receive-side resource bounds. Offers are auto-accepted, so without these a
// malicious or buggy contact could open many transfers (or start one and never
// finish) and grow browser memory without limit. Cap concurrent incoming
// transfers and reclaim any that goes idle. Worst-case buffered memory is then
// MAX_CONCURRENT_INCOMING * MAX_FILE_BYTES.
const MAX_CONCURRENT_INCOMING = 8;
const TRANSFER_IDLE_TIMEOUT_MS = 60 * 1000;

/**
 * @param {object} deps
 * @param {(cmd:string, payload:object)=>void} deps.sendCmd
 * @param {(msg:string, kind?:string)=>void} deps.showToast
 * @param {(msg:object)=>void} deps.renderMessage
 * @param {()=>object|null} deps.getActiveView
 */
import { announce } from './a11y.js';

export function createFileTransfer({ sendCmd, showToast, renderMessage, getActiveView, resolveSender }) {
    const _outgoingFiles = {};   // file_id -> {resolve, reject, onAck, onFail, onReceived}
    const _incomingFiles = {};   // file_id -> {meta, chunks[], received, total, fromWebid}

    // `verb` is a discriminator ('sending' | 'receiving'), not display text, so the
    // visible label, aria-label, and spoken announcement all come from locale keys.
    function _showTransferProgress(fileId, name, pct, verb) {
        const isSend = verb === 'sending';
        const labelText = t(isSend ? 'file.labelSending' : 'file.labelReceiving', { name });
        const progressText = t(isSend ? 'file.progressSending' : 'file.progressReceiving', { name, pct });
        let el = document.getElementById("xfer-" + fileId);
        if (!el) {
            el = document.createElement("div");
            el.id = "xfer-" + fileId;
            // progressbar semantics (aria-valuenow updated below). NOT aria-live —
            // per-% updates would flood a screen reader; instead announce() below
            // speaks the state only at quarter marks.
            if (el.setAttribute) {   // real DOM only (vitest stubs createElement)
                el.setAttribute("role", "progressbar");
                el.setAttribute("aria-label", labelText);
                el.setAttribute("aria-valuemin", "0");
                el.setAttribute("aria-valuemax", "100");
            }
            el.className = "xfer-progress";
            document.body.appendChild(el);
        }
        if (el.setAttribute) el.setAttribute("aria-valuenow", String(pct));
        el.textContent = progressText;   // visible: smooth updates
        const _step = Math.floor(pct / 25);
        if (el._lastStep !== _step) { el._lastStep = _step; announce(progressText); }
    }
    function _clearTransferProgress(fileId) {
        const el = document.getElementById("xfer-" + fileId);
        if (el) el.remove();
    }
    // Free an incoming transfer's buffered chunks, idle timer, and progress UI.
    function _dropIncoming(fileId) {
        const rec = _incomingFiles[fileId];
        if (!rec) return;
        if (rec._idleTimer) { clearTimeout(rec._idleTimer); rec._idleTimer = null; }
        delete _incomingFiles[fileId];
        _clearTransferProgress(fileId);
    }
    // (Re)arm the idle watchdog. If no new chunk arrives within the window the
    // transfer is treated as abandoned and its memory is reclaimed, so a peer
    // that offers a file and then stalls cannot pin buffered chunks forever.
    function _armIdleTimeout(fileId) {
        const rec = _incomingFiles[fileId];
        if (!rec) return;
        if (rec._idleTimer) clearTimeout(rec._idleTimer);
        rec._idleTimer = setTimeout(() => {
            if (!_incomingFiles[fileId]) return;
            sendCmd("file_reject", { to_webid: rec.fromWebid, file_id: fileId, reason: "incomplete" });
            showToast(t('file.transferFailed', {
                filename: rec.meta.filename, received: rec.received, total: rec.total,
            }), "error");
            _dropIncoming(fileId);
        }, TRANSFER_IDLE_TIMEOUT_MS);
    }

    // opts.seal(bytes, meta) -> {hdr, ct}: for a DM peer with a key, encrypts
    // the file and seals its key (e2e-secondary.js). The chunks then carry only
    // ciphertext and the offer only the sealed header; a throw aborts the send.
    async function sendFileChunked(file, toWebid, opts = {}) {
        const fileId = (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36)).replace(/-/g, "").slice(0, 16);
        let buf = new Uint8Array(await file.arrayBuffer());
        let offer = {
            filename: file.name, mime_type: file.type || "application/octet-stream", size_bytes: buf.length,
        };
        if (opts.seal) {
            const sealed = await opts.seal(buf, { name: file.name, mime: file.type || "application/octet-stream", size: buf.length });
            if (!sealed) throw new Error("seal_failed");
            buf = sealed.ct;
            offer = { filename: SEALED_FILE_NAME, mime_type: SEALED_FILE_MIME, size_bytes: buf.length, ...sealed.hdr };
        }
        const total = Math.ceil(buf.length / CHUNK_BYTES) || 1;
        const rec = { stage: "offer" };
        _outgoingFiles[fileId] = rec;
        const acceptP = new Promise((resolve, reject) => {
            rec.resolve = resolve; rec.reject = reject;
            setTimeout(() => reject(new Error("accept_timeout")), 30000);
        });
        sendCmd("file_offer", { to_webid: toWebid, file_id: fileId, ...offer, total_chunks: total });
        showToast(t('file.offering', { filename: file.name }));
        try { await acceptP; } catch (e) { delete _outgoingFiles[fileId]; _clearTransferProgress(fileId); showToast(t('file.notAccepted')); return false; }

        // Chunks, paced by acks. `wake` is resolved by every ack or failure.
        rec.stage = "sending";
        const acked = new Set();
        let failure = null, wake = null;
        const poke = () => { if (wake) { const w = wake; wake = null; w(); } };
        rec.onAck = (seq) => { if (Number.isInteger(seq) && seq >= 0 && seq < total) acked.add(seq); poke(); };
        rec.onFail = (reason) => { failure = failure || reason || "failed"; poke(); };
        const waitForProgress = () => new Promise((resolve) => {
            const timer = setTimeout(() => { failure = failure || "ack_timeout"; resolve(); }, CHUNK_ACK_TIMEOUT_MS);
            wake = () => { clearTimeout(timer); resolve(); };
        });
        const fail = () => {
            delete _outgoingFiles[fileId];
            _clearTransferProgress(fileId);
            showToast(t('file.deliveryFailed', { filename: file.name }), "error");
            return false;
        };
        for (let seq = 0; seq < total; seq++) {
            while (!failure && seq - acked.size >= CHUNK_WINDOW) await waitForProgress();
            if (failure) return fail();
            const slice = buf.subarray(seq * CHUNK_BYTES, (seq + 1) * CHUNK_BYTES);
            sendCmd("file_chunk", { to_webid: toWebid, file_id: fileId, seq, data: u8ToB64(slice) });
            _showTransferProgress(fileId, file.name, Math.round(((seq + 1) / total) * 100), "sending");
        }
        while (!failure && acked.size < total) await waitForProgress();
        if (failure) return fail();

        // Every chunk is through; wait for the receiver to confirm the file.
        rec.stage = "complete";
        const receiptP = new Promise((resolve) => {
            const timer = setTimeout(() => resolve(false), RECEIPT_TIMEOUT_MS);
            rec.onReceived = (ok) => { clearTimeout(timer); resolve(ok); };
            rec.onFail = () => { clearTimeout(timer); resolve(false); };
        });
        sendCmd("file_complete", { to_webid: toWebid, file_id: fileId });
        const ok = await receiptP;
        if (!ok) return fail();
        _clearTransferProgress(fileId);
        delete _outgoingFiles[fileId];
        showToast(t('file.sent', { filename: file.name }));
        return true;
    }

    // A sealed offer arrives already opened by the caller (_sealedFile holds
    // the real metadata and file key) or marked _sealedBad.
    function handleFileOffer(event) {
        if (event._sealedBad) {
            sendCmd("file_reject", { to_webid: event.from_webid, file_id: event.file_id, reason: "bad_offer" });
            return;
        }
        const sealed = event._sealedFile || null;
        if (event.size_bytes > MAX_FILE_BYTES + (sealed ? FILE_TAG_BYTES : 0)) {
            sendCmd("file_reject", { to_webid: event.from_webid, file_id: event.file_id, reason: "too_large" });
            return;
        }
        // total_chunks is attacker-controlled — reject nonsensical counts before
        // allocating an array from it (new Array(huge) / mismatched size).
        const _maxChunks = Math.ceil((MAX_FILE_BYTES + FILE_TAG_BYTES) / CHUNK_BYTES) + 1;
        const _tc = event.total_chunks;
        if (!Number.isInteger(_tc) || _tc <= 0 || _tc > _maxChunks) {
            sendCmd("file_reject", { to_webid: event.from_webid, file_id: event.file_id, reason: "bad_offer" });
            return;
        }
        // Cap concurrent transfers. Offers auto-accept, so this stops a peer from
        // opening many at once to exhaust memory; existing ones free on
        // completion or via the idle watchdog below.
        if (Object.keys(_incomingFiles).length >= MAX_CONCURRENT_INCOMING) {
            sendCmd("file_reject", { to_webid: event.from_webid, file_id: event.file_id, reason: "busy" });
            return;
        }
        _incomingFiles[event.file_id] = {
            meta: { filename: event.filename, mime_type: event.mime_type, size: event.size_bytes },
            chunks: new Array(event.total_chunks), received: 0, total: event.total_chunks,
            fromWebid: event.from_webid, sealed,
        };
        _armIdleTimeout(event.file_id);
        sendCmd("file_accept", { to_webid: event.from_webid, file_id: event.file_id });
        showToast(t('file.receiving', { filename: event.filename }));
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
            _armIdleTimeout(event.file_id);   // fresh chunk = still active
            _showTransferProgress(event.file_id, rec.meta.filename, Math.round((rec.received / rec.total) * 100), "receiving");
        }
    }
    function handleFileComplete(event) {
        const rec = _incomingFiles[event.file_id];
        if (!rec) return;
        if (rec._idleTimer) { clearTimeout(rec._idleTimer); rec._idleTimer = null; }
        _clearTransferProgress(event.file_id);
        // Verify every chunk actually arrived. Without this, a dropped or
        // never-delivered chunk is silently filled with empty bytes below,
        // producing a corrupt file the user can't tell apart from a good one.
        if (rec.received < rec.total) {
            delete _incomingFiles[event.file_id];
            sendCmd("file_reject", { to_webid: rec.fromWebid, file_id: event.file_id, reason: "incomplete" });
            showToast(t('file.transferFailed', { filename: rec.meta.filename, received: rec.received, total: rec.total }), "error");
            return;
        }
        let totalLen = 0;
        const parts = [];
        for (let i = 0; i < rec.total; i++) { const u8 = b64ToU8(rec.chunks[i] || ""); parts.push(u8); totalLen += u8.length; }
        const full = new Uint8Array(totalLen);
        let off = 0;
        for (const p of parts) { full.set(p, off); off += p.length; }
        const fromWebid = rec.fromWebid;
        delete _incomingFiles[event.file_id];
        const av = getActiveView();
        // Cross-gateway, fromWebid is the peer's gateway did: label the message
        // with the contact's name (and thread) rather than a slice of that key.
        const who = (resolveSender && resolveSender(fromWebid)) || {};
        const threadId = (av && av.peerWebid === fromWebid) ? av.id : (who.threadId || fromWebid);
        const render = (bytes, extra = {}) => {
            renderMessage({
                type: "message", source: "relay", thread_id: threadId,
                from_webid: fromWebid, from_display_name: who.name || dmFallbackName(fromWebid),
                content: `📎 ${rec.meta.filename}`, timestamp: new Date().toISOString(),
                message_id: "file-" + event.file_id, local: true,
                file: { filename: rec.meta.filename, mime_type: rec.meta.mime_type, size: bytes.length, data_b64: u8ToB64(bytes), ...extra },
            });
            sendCmd("file_received", { to_webid: fromWebid, file_id: event.file_id });
        };
        if (!rec.sealed) { render(full, { size: rec.meta.size }); return; }
        const s = rec.sealed;
        const dims = s.width ? { width: s.width, height: s.height } : {};
        return openFileBytes(full, s)
            .then((bytes) => render(bytes, { spoiler: s.spoiler === true, ...dims }))
            .catch(() => {
                sendCmd("file_reject", { to_webid: fromWebid, file_id: event.file_id, reason: "decrypt_failed" });
                showToast(t('file.decryptFailed', { filename: rec.meta.filename }), "error");
            });
    }
    function handleFileAccept(event) {
        const rec = _outgoingFiles[event.file_id];
        if (rec && rec.resolve) rec.resolve();
    }
    // Once accepted, a reject means the receiver could not assemble or open
    // the file; the sending loop reports that as a failed delivery.
    function handleFileReject(event) {
        const rec = _outgoingFiles[event.file_id];
        if (!rec) return;
        if (rec.stage !== "offer") { rec.onFail?.(event.reason || "rejected"); return; }
        rec.reject(new Error(event.reason || "rejected"));
        delete _outgoingFiles[event.file_id];
        _clearTransferProgress(event.file_id);
        showToast(t('file.declined'));
    }
    function handleFileUnreachable(event) {
        const rec = _outgoingFiles[event.file_id];
        if (!rec) return;
        if (rec.stage !== "offer") { rec.onFail?.("unreachable"); return; }
        rec.reject(new Error("unreachable"));
        delete _outgoingFiles[event.file_id];
        _clearTransferProgress(event.file_id);
        showToast(t('file.recipientOffline'));
    }
    function handleFileChunkAck(event) {
        _outgoingFiles[event.file_id]?.onAck?.(event.seq);
    }
    function handleFileSendFailed(event) {
        _outgoingFiles[event.file_id]?.onFail?.(event.reason || "relay_failed");
    }
    function handleFileReceived(event) {
        _outgoingFiles[event.file_id]?.onReceived?.(true);
    }

    return {
        MAX_FILE_BYTES, MAX_CONCURRENT_INCOMING, TRANSFER_IDLE_TIMEOUT_MS,
        sendFileChunked,
        handleFileOffer, handleFileChunk, handleFileComplete,
        handleFileAccept, handleFileReject, handleFileUnreachable,
        handleFileChunkAck, handleFileSendFailed, handleFileReceived,
        // Exposed for tests: how many incoming transfers are buffered right now.
        _incomingCount: () => Object.keys(_incomingFiles).length,
    };
}
