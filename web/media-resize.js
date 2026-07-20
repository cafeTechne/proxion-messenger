// media-resize.js — R59B: auto-downscale images so they fit a room's 512 KB
// inline-attachment cap (the chunked 25 MB path is DM-only). Without this, a
// normal phone photo simply cannot be posted to a room.
//
// needsDownscale is pure (unit-tested); downscaleImage is browser-only
// (canvas) and verified by the live smoke.

// Raw target under the gateway's 512 KB decoded guard, with headroom.
export const DOWNSCALE_TARGET_BYTES = 500 * 1024;
export const DOWNSCALE_MAX_DIM = 2048;
const ROOM_INLINE_LIMIT = 524288;

// Animated GIFs are excluded: canvas re-encode would freeze them to one frame.
const _RESIZABLE = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/avif']);

export function needsDownscale(file, viewType) {
    if (!file || file.size <= ROOM_INLINE_LIMIT) return false;
    const isDm = viewType === 'dm' || viewType === 'local_dm';
    if (isDm) return false;   // DMs have the 25 MB chunked path
    return _RESIZABLE.has((file.type || '').toLowerCase());
}

// Quality steps tried at each size; then dimensions halve and we retry.
export function downscalePlan(maxDim) {
    const plan = [];
    let dim = maxDim;
    while (dim >= 256) {
        for (const q of [0.85, 0.7, 0.5]) plan.push({ dim, q });
        dim = Math.floor(dim / 2);
    }
    return plan;
}

function _toBlob(canvas, type, q) {
    return new Promise((resolve) => canvas.toBlob(resolve, type, q));
}

// Returns a new, smaller File (webp), or throws if even the smallest step
// can't fit the target (callers fall through to the existing size toasts).
export async function downscaleImage(file, {
    maxBytes = DOWNSCALE_TARGET_BYTES,
    maxDim = DOWNSCALE_MAX_DIM,
} = {}) {
    const bitmap = await createImageBitmap(file);
    try {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        for (const { dim, q } of downscalePlan(maxDim)) {
            const scale = Math.min(1, dim / Math.max(bitmap.width, bitmap.height));
            canvas.width = Math.max(1, Math.round(bitmap.width * scale));
            canvas.height = Math.max(1, Math.round(bitmap.height * scale));
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
            const blob = await _toBlob(canvas, 'image/webp', q);
            if (blob && blob.size <= maxBytes) {
                const base = (file.name || 'image').replace(/\.[a-z0-9]+$/i, '');
                return new File([blob], `${base}.webp`, { type: 'image/webp' });
            }
        }
    } finally {
        bitmap.close?.();
    }
    throw new Error('downscale_failed');
}
