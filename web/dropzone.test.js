// dropzone.js: drag depth counter, file-only filtering, multi-file drops.
import { describe, it, expect } from 'vitest';
import { dragHasFiles, droppedFiles, createDropZone } from './dropzone.js';

function mkEl() {
    const handlers = {};
    const cls = new Set();
    return {
        handlers,
        addEventListener: (type, fn) => { handlers[type] = fn; },
        classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c) },
    };
}
function ev(types, files = []) {
    const e = { dataTransfer: { types, files, dropEffect: 'none' }, prevented: false };
    e.preventDefault = () => { e.prevented = true; };
    return e;
}
const FILES = ['Files'];

describe('dragHasFiles', () => {
    it('accepts arrays and DOMStringList-style types', () => {
        expect(dragHasFiles({ types: ['text/plain', 'Files'] })).toBe(true);
        expect(dragHasFiles({ types: { contains: (t) => t === 'Files' } })).toBe(true);
    });
    it('rejects text and link drags and missing data', () => {
        expect(dragHasFiles({ types: ['text/plain', 'text/uri-list'] })).toBe(false);
        expect(dragHasFiles(null)).toBe(false);
        expect(dragHasFiles({})).toBe(false);
    });
});

describe('droppedFiles', () => {
    it('returns every file', () => {
        expect(droppedFiles({ files: ['a', 'b', 'c'] })).toEqual(['a', 'b', 'c']);
        expect(droppedFiles(null)).toEqual([]);
    });
});

describe('createDropZone', () => {
    it('stays highlighted while the pointer crosses child elements', () => {
        const el = mkEl();
        const z = createDropZone(el, {});
        el.handlers.dragenter(ev(FILES));          // enter the feed
        el.handlers.dragenter(ev(FILES));          // enter a message inside it
        el.handlers.dragleave(ev(FILES));          // the feed itself is left for the message
        expect(el.classList.contains('drop-active')).toBe(true);
        expect(z.depth()).toBe(1);
        el.handlers.dragleave(ev(FILES));          // leave the feed for real
        expect(el.classList.contains('drop-active')).toBe(false);
        expect(z.depth()).toBe(0);
    });

    it('ignores text drags entirely', () => {
        const el = mkEl();
        createDropZone(el, {});
        const e = ev(['text/plain']);
        el.handlers.dragenter(e);
        el.handlers.dragover(e);
        expect(el.classList.contains('drop-active')).toBe(false);
        expect(e.prevented).toBe(false);
    });

    it('allows the drop and asks for a copy on file drags', () => {
        const el = mkEl();
        createDropZone(el, {});
        const e = ev(FILES);
        el.handlers.dragover(e);
        expect(e.prevented).toBe(true);
        expect(e.dataTransfer.dropEffect).toBe('copy');
    });

    it('hands over every dropped file and clears the highlight', () => {
        const el = mkEl();
        let got = null;
        const z = createDropZone(el, { onFiles: (f) => { got = f; } });
        el.handlers.dragenter(ev(FILES));
        el.handlers.drop(ev(FILES, ['a.png', 'b.png', 'c.pdf']));
        expect(got).toEqual(['a.png', 'b.png', 'c.pdf']);
        expect(el.classList.contains('drop-active')).toBe(false);
        expect(z.depth()).toBe(0);
    });

    it('does not call onFiles for a drop without files', () => {
        const el = mkEl();
        let called = false;
        createDropZone(el, { onFiles: () => { called = true; } });
        el.handlers.drop(ev(['text/plain']));
        expect(called).toBe(false);
    });

    it('reset() clears a highlight left by an aborted drag', () => {
        const el = mkEl();
        const z = createDropZone(el, {});
        el.handlers.dragenter(ev(FILES));
        z.reset();
        expect(el.classList.contains('drop-active')).toBe(false);
        expect(z.depth()).toBe(0);
    });
});
