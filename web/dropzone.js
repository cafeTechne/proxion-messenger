// dropzone.js: file drop targets for the conversation (feed and composer).
//
// Three things the plain dragover/dragleave pair got wrong:
//  - dragleave fires every time the pointer crosses into a child element, so
//    the highlight flickered over messages. An enter/leave depth counter keeps
//    it on until the pointer really leaves the target.
//  - dragging selected text or a link lit the target up too. Only drags whose
//    dataTransfer.types include "Files" count.
//  - only files[0] was sent. Every dropped file is handed to onFiles.

// True when a drag carries files (DOMStringList in older engines, array now).
export function dragHasFiles(dt) {
    const types = dt && dt.types;
    if (!types) return false;
    if (typeof types.includes === 'function') return types.includes('Files');
    if (typeof types.contains === 'function') return types.contains('Files');
    return Array.from(types).includes('Files');
}

export function droppedFiles(dt) {
    return Array.from((dt && dt.files) || []).filter(Boolean);
}

// Wires el as a drop zone. Returns { depth(), reset() } for tests and for a
// global cleanup (Esc mid-drag, a drop elsewhere).
export function createDropZone(el, { onFiles, activeClass = 'drop-active' } = {}) {
    let depth = 0;
    const reset = () => { depth = 0; el.classList.remove(activeClass); };
    el.addEventListener('dragenter', (e) => {
        if (!dragHasFiles(e.dataTransfer)) return;
        e.preventDefault();
        depth++;
        el.classList.add(activeClass);
    });
    el.addEventListener('dragover', (e) => {
        if (!dragHasFiles(e.dataTransfer)) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    });
    el.addEventListener('dragleave', (e) => {
        if (!dragHasFiles(e.dataTransfer)) return;
        depth = Math.max(0, depth - 1);
        if (depth === 0) el.classList.remove(activeClass);
    });
    el.addEventListener('drop', (e) => {
        reset();
        const files = droppedFiles(e.dataTransfer);
        if (!files.length) return;
        e.preventDefault();
        if (onFiles) onFiles(files);
    });
    return { depth: () => depth, reset };
}
