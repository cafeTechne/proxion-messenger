// gifmotion.js: animated-image detection and the play/pause state machine.
import { describe, it, expect, afterEach } from 'vitest';
import {
    isAnimatedImage, initialGifState, gifToggleHtml, freezeImage, playImage, setGifState, toggleGif,
} from './gifmotion.js';

// RIFF....WEBPVP8X + 4-byte chunk size + flags byte (offset 20).
function webpHeader(flags) {
    const bytes = 'RIFF\x00\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00' + String.fromCharCode(flags) + '\x00\x00\x00';
    return btoa(bytes + '\x00'.repeat(24 - bytes.length));
}

describe('isAnimatedImage', () => {
    it('treats every GIF as animated', () => {
        expect(isAnimatedImage('image/gif', '')).toBe(true);
        expect(isAnimatedImage('IMAGE/GIF')).toBe(true);
    });
    it('reads the VP8X animation flag for WebP', () => {
        expect(isAnimatedImage('image/webp', webpHeader(0x02))).toBe(true);
        expect(isAnimatedImage('image/webp', webpHeader(0x10))).toBe(false);
        expect(isAnimatedImage('image/webp', 'short')).toBe(false);
        expect(isAnimatedImage('image/webp', '!!!!'.repeat(10))).toBe(false);
    });
    it('ignores still formats', () => {
        expect(isAnimatedImage('image/png', 'AAAA'.repeat(10))).toBe(false);
        expect(isAnimatedImage('', '')).toBe(false);
    });
});

describe('initialGifState', () => {
    afterEach(() => { delete globalThis.matchMedia; });
    it('starts paused only under prefers-reduced-motion', () => {
        globalThis.matchMedia = (q) => ({ matches: q.includes('reduce') });
        expect(initialGifState()).toBe('paused');
        globalThis.matchMedia = () => ({ matches: false });
        expect(initialGifState()).toBe('playing');
    });
});

describe('gifToggleHtml', () => {
    it('is a labelled button that offers the opposite action', () => {
        expect(gifToggleHtml('playing')).toMatch(/^<button type="button" class="gif-toggle" aria-label="gif.pause"/);
        expect(gifToggleHtml('paused')).toContain('aria-label="gif.play"');
    });
});

function mkImg({ complete = true, naturalWidth = 10 } = {}) {
    const attrs = { src: 'data:image/gif;base64,AAAA' };
    return {
        complete, naturalWidth, naturalHeight: 10, dataset: {},
        getAttribute: (k) => attrs[k],
        get src() { return attrs.src; }, set src(v) { attrs.src = v; },
    };
}
const canvasDoc = (url) => ({ createElement: () => ({ getContext: () => ({ drawImage() {} }), toDataURL: () => url }) });

describe('freezeImage / playImage', () => {
    afterEach(() => { delete globalThis.document; });
    it('waits for an image that has not decoded', () => {
        const img = mkImg({ complete: false, naturalWidth: 0 });
        expect(freezeImage(img)).toBe(false);
        expect(img.dataset.animSrc).toBeUndefined();
    });
    it('swaps to a canvas still and back', () => {
        globalThis.document = canvasDoc('data:image/png;base64,STILL');
        const img = mkImg();
        expect(freezeImage(img)).toBe(true);
        expect(img.src).toBe('data:image/png;base64,STILL');
        expect(img.dataset.animSrc).toBe('data:image/gif;base64,AAAA');
        playImage(img);
        expect(img.src).toBe('data:image/gif;base64,AAAA');
        expect(img.dataset.animSrc).toBeUndefined();
    });
    it('marks the image even when the canvas cannot be read, so it never stays hidden', () => {
        globalThis.document = { createElement: () => ({ getContext: () => { throw new Error('tainted'); } }) };
        const img = mkImg();
        expect(freezeImage(img)).toBe(true);
        expect(img.dataset.animSrc).toBe('data:image/gif;base64,AAAA');
    });
});

describe('setGifState / toggleGif', () => {
    afterEach(() => { delete globalThis.document; });
    it('flips the wrapper state and relabels the button in place', () => {
        globalThis.document = canvasDoc('data:,still');
        const img = mkImg();
        const btn = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, title: '', innerHTML: '' };
        const wrap = { dataset: { gif: 'playing' }, querySelector: () => btn, querySelectorAll: () => [img] };
        toggleGif(wrap);
        expect(wrap.dataset.gif).toBe('paused');
        expect(btn.attrs['aria-label']).toBe('gif.play');
        expect(img.src).toBe('data:,still');
        setGifState(wrap, 'playing');
        expect(btn.attrs['aria-label']).toBe('gif.pause');
        expect(img.src).toBe('data:image/gif;base64,AAAA');
    });
});
