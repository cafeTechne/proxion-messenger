// gifmotion.js: pause control for animated images (GIF, animated WebP).
//
// An animated GIF loops forever, and WCAG 2.2.2 (Pause, Stop, Hide) asks for a
// way to stop motion that lasts more than five seconds. Every animated
// attachment gets a play/pause button; under prefers-reduced-motion it starts
// paused. Pausing draws the current frame to a canvas and swaps the <img> to
// that still, keeping the animated source in data-anim-src for play.
//
// Markup contract (rendering.js and gifs.js build it):
//   <span class="gif-wrap" data-gif="playing|paused">
//     ...<img>...  <button class="gif-toggle">
//   </span>

import { t } from './i18n.js';
import { icon } from './icons.js';

const _ANIMATED_MIMES = new Set(['image/gif', 'image/apng']);

// True for a GIF, or a WebP whose VP8X header has the animation flag set
// (bytes 12..15 "VP8X", flag bit 0x02 in byte 20). Only the first 32 base64
// characters are decoded.
export function isAnimatedImage(mime, b64 = '') {
    const m = String(mime || '').toLowerCase();
    if (_ANIMATED_MIMES.has(m)) return true;
    if (m !== 'image/webp' || typeof b64 !== 'string' || b64.length < 32) return false;
    let head;
    try { head = atob(b64.slice(0, 32)); } catch (_) { return false; }
    return head.slice(0, 4) === 'RIFF' && head.slice(8, 12) === 'WEBP'
        && head.slice(12, 16) === 'VP8X' && (head.charCodeAt(20) & 0x02) !== 0;
}

export function prefersReducedMotion() {
    try {
        return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (_) { return false; }
}

// Initial state for a newly drawn animated image.
export function initialGifState() {
    return prefersReducedMotion() ? 'paused' : 'playing';
}

function _toggleParts(state) {
    const paused = state === 'paused';
    return { label: t(paused ? 'gif.play' : 'gif.pause'), svg: icon(paused ? 'play' : 'pause', { size: 14 }) };
}

export function gifToggleHtml(state) {
    const { label, svg } = _toggleParts(state);
    return `<button type="button" class="gif-toggle" aria-label="${label}" title="${label}">${svg}</button>`;
}

// Swap a loaded <img> to a still of its current frame. Returns false when the
// image has not decoded yet (the load listener retries). If the canvas cannot
// be read the image is marked anyway and keeps animating, so it never stays
// hidden.
export function freezeImage(img) {
    if (!img || img.dataset.animSrc) return true;
    if (!img.complete || !img.naturalWidth) return false;
    const src = img.getAttribute('src') || '';
    try {
        const c = document.createElement('canvas');
        c.width = img.naturalWidth;
        c.height = img.naturalHeight;
        c.getContext('2d').drawImage(img, 0, 0);
        const still = c.toDataURL('image/png');
        img.dataset.animSrc = src;
        img.src = still;
    } catch (_) {
        img.dataset.animSrc = src;
    }
    return true;
}

export function playImage(img) {
    if (!img || !img.dataset.animSrc) return;
    const src = img.dataset.animSrc;
    delete img.dataset.animSrc;
    img.src = src;
}

export function setGifState(wrap, state) {
    if (!wrap) return;
    wrap.dataset.gif = state;
    const btn = wrap.querySelector('.gif-toggle');
    if (btn) {
        const { label, svg } = _toggleParts(state);
        btn.setAttribute('aria-label', label);
        btn.title = label;
        btn.innerHTML = svg;
    }
    for (const img of wrap.querySelectorAll('img')) {
        if (state === 'paused') freezeImage(img);
        else playImage(img);
    }
}

export function toggleGif(wrap) {
    setGifState(wrap, wrap?.dataset.gif === 'paused' ? 'playing' : 'paused');
}

// One listener pair per container: freeze images that finish loading inside a
// paused wrapper, and toggle on the play/pause button. load does not bubble,
// hence the capture listener.
export function attachGifMotion(root) {
    if (!root || root._gifMotion) return;
    root._gifMotion = true;
    root.addEventListener('load', (e) => {
        const img = e.target;
        if (!img || img.tagName !== 'IMG' || img.dataset.animSrc) return;
        const wrap = img.closest('.gif-wrap');
        if (wrap && wrap.dataset.gif === 'paused') freezeImage(img);
    }, true);
    root.addEventListener('click', (e) => {
        const btn = e.target.closest && e.target.closest('.gif-toggle');
        if (!btn || !root.contains(btn)) return;
        e.preventDefault();
        e.stopPropagation();
        toggleGif(btn.closest('.gif-wrap'));
    });
}
