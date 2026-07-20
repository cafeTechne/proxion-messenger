// meme.js — R60B: the meme generator. Classic top/bottom captions drawn on
// any image, entirely client-side (canvas), output rides the normal
// sendAttachmentFile pipeline (E2E, room downscale, optimistic render).
// Entry points: the "Meme" button in the GIF tray and a caption action on
// image messages.
//
// wrapCaption / captionFontPx are pure and unit-tested; the canvas paint is
// browser-only and covered by the live smoke.

import { t } from './i18n.js';

export const MEME_MAX_DIM = 1024;

// Greedy word-wrap by character budget (canvas measure refines nothing here —
// the classic meme look tolerates approximation and this stays pure/testable).
export function wrapCaption(text, maxCharsPerLine, maxLines = 3) {
    const words = (text || '').trim().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    const lines = [];
    let cur = '';
    for (const w of words) {
        const candidate = cur ? cur + ' ' + w : w;
        if (candidate.length <= maxCharsPerLine || !cur) {
            cur = candidate;
        } else {
            lines.push(cur);
            cur = w;
        }
    }
    if (cur) lines.push(cur);
    if (lines.length > maxLines) {
        const kept = lines.slice(0, maxLines);
        kept[maxLines - 1] = kept[maxLines - 1] + '…';
        return kept;
    }
    return lines;
}

// Font size scales with image height, shrinking as the caption grows.
export function captionFontPx(canvasH, textLen) {
    const base = Math.round(canvasH / 8);
    const shrink = textLen > 40 ? 0.7 : textLen > 20 ? 0.85 : 1;
    return Math.max(14, Math.round(base * shrink));
}

// Browser-only: draw image + captions onto a canvas and return it.
export function renderMeme(bitmap, { top = '', bottom = '' } = {}, canvas = null) {
    const scale = Math.min(1, MEME_MAX_DIM / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const cv = canvas || document.createElement('canvas');
    cv.width = w; cv.height = h;
    const ctx = cv.getContext('2d');
    ctx.drawImage(bitmap, 0, 0, w, h);

    const draw = (text, fromTop) => {
        const clean = (text || '').trim().toUpperCase();
        if (!clean) return;
        const size = captionFontPx(h, clean.length);
        const maxChars = Math.max(8, Math.floor(w / (size * 0.62)));
        const lines = wrapCaption(clean, maxChars);
        ctx.font = `bold ${size}px Impact, 'Arial Black', sans-serif`;
        ctx.textAlign = 'center';
        ctx.fillStyle = '#fff';
        ctx.strokeStyle = '#000';
        ctx.lineWidth = Math.max(2, Math.round(size / 9));
        ctx.lineJoin = 'round';
        lines.forEach((line, i) => {
            const y = fromTop
                ? size * 1.05 * (i + 1)
                : h - size * 1.05 * (lines.length - 1 - i) - size * 0.25;
            ctx.strokeText(line, w / 2, y, w - 12);
            ctx.fillText(line, w / 2, y, w - 12);
        });
    };
    draw(top, true);
    draw(bottom, false);
    return cv;
}

export function createMeme({ showToast, sendAttachmentFile }) {
    let _bitmap = null;

    function _els() {
        return {
            modal: document.getElementById('meme-modal'),
            canvas: document.getElementById('meme-canvas'),
            top: document.getElementById('meme-top'),
            bottom: document.getElementById('meme-bottom'),
        };
    }

    function _repaint() {
        const { canvas, top, bottom } = _els();
        if (_bitmap && canvas) renderMeme(_bitmap, { top: top?.value, bottom: bottom?.value }, canvas);
    }

    async function openWithBlob(blob) {
        try {
            _bitmap = await createImageBitmap(blob);
        } catch (_) {
            showToast(t('meme.badImage'));
            return;
        }
        const { modal, top, bottom } = _els();
        if (!modal) return;
        if (top) top.value = '';
        if (bottom) bottom.value = '';
        modal.style.display = 'flex';
        _repaint();
        setTimeout(() => top?.focus(), 50);
    }

    function closeMeme() {
        const { modal } = _els();
        if (modal) modal.style.display = 'none';
        _bitmap?.close?.();
        _bitmap = null;
    }

    async function sendMeme() {
        const { canvas } = _els();
        if (!canvas || !_bitmap) return;
        _repaint();
        const blob = await new Promise(res => canvas.toBlob(res, 'image/webp', 0.9));
        if (!blob) { showToast(t('meme.badImage')); return; }
        closeMeme();
        await sendAttachmentFile(new File([blob], 'meme.webp', { type: 'image/webp' }));
    }

    function wireMeme() {
        document.getElementById('meme-btn')?.addEventListener('click', () => {
            document.getElementById('meme-file-input')?.click();
        });
        document.getElementById('meme-file-input')?.addEventListener('change', async (e) => {
            const file = e.target.files?.[0];
            if (file) await openWithBlob(file);
            e.target.value = '';
        });
        document.getElementById('meme-top')?.addEventListener('input', _repaint);
        document.getElementById('meme-bottom')?.addEventListener('input', _repaint);
        document.getElementById('meme-cancel')?.addEventListener('click', closeMeme);
        document.getElementById('meme-send')?.addEventListener('click', sendMeme);
    }

    return { openWithBlob, closeMeme, sendMeme, wireMeme };
}
