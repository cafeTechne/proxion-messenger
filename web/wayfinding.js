// wayfinding.js: tell people where they are when the page itself does not
// reload. The document title names the open conversation (WCAG 2.4.2), and the
// favicon carries an unread dot while the tab is in the background.
import { t } from './i18n.js';

// "#general" for rooms, the plain name for DMs. Empty when nothing is open.
export function viewLabel(view) {
    if (!view || !view.name) return '';
    const isRoom = view.type === 'room' || view.type === 'local_room';
    return isRoom ? `#${view.name}` : String(view.name);
}

// "(3) #general · Proxion", "Alice · Proxion", "(2) Proxion", "Proxion".
// `tr` is injectable for tests.
export function buildPageTitle(unread, view, tr = t) {
    const label = viewLabel(view);
    const base = label ? tr('page.titleView', { view: label }) : 'Proxion';
    return unread > 0 ? `(${unread}) ${base}` : base;
}

// ---- Jump to a message ----
//
// Used by reply quotes, pins and saved messages. One scroll (scroll-margin on
// the row keeps it off the feed's edge), then focus without a second scroll,
// then a brief CSS highlight (.message--jumped: an animation that also has a
// forced-colors outline and a reduced-motion variant). The row becomes the
// feed's roving tab stop so Tab and the arrow keys continue from there.
export function jumpToMessage(el, behavior = 'auto') {
    if (!el) return false;
    el.scrollIntoView({ behavior, block: 'start' });
    const feed = el.parentElement ? el.closest('#message-feed') : null;
    if (feed) {
        feed.querySelectorAll('.message[tabindex="0"]').forEach((m) => {
            if (m !== el) m.tabIndex = -1;
        });
    }
    el.tabIndex = 0;
    try { el.focus({ preventScroll: true }); } catch { /* detached */ }
    el.classList.remove('message--jumped');
    void el.offsetWidth; // restart the animation on a repeat jump
    el.classList.add('message--jumped');
    clearTimeout(el._jumpTimer);
    el._jumpTimer = setTimeout(() => el.classList.remove('message--jumped'), 2000);
    return true;
}

// ---- Favicon unread dot ----
//
// Draws the app icon onto a canvas with a dot in the corner, once, and swaps
// the <link rel="icon"> href between the original and the dotted copy. The dot
// color comes from the theme tokens so JS stays free of color literals.

const ICON_SRC = 'icons/icon-192.png';
const SIZE = 64;

export function createFaviconBadge(doc = (typeof document !== 'undefined' ? document : null)) {
    const link = doc && doc.querySelector ? doc.querySelector('link[rel~="icon"]') : null;
    if (!link) return { update() {} };
    const original = { href: link.getAttribute('href'), type: link.getAttribute('type') };
    let dotted = null;      // data URL once drawn
    let drawing = null;     // pending draw
    let want = false;

    function draw() {
        if (drawing) return drawing;
        drawing = new Promise((resolve) => {
            const img = new Image();
            img.onload = () => {
                try {
                    const canvas = doc.createElement('canvas');
                    canvas.width = SIZE; canvas.height = SIZE;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0, SIZE, SIZE);
                    const css = getComputedStyle(doc.documentElement);
                    const dot = css.getPropertyValue('--color-danger').trim();
                    const ring = css.getPropertyValue('--bg-primary').trim();
                    const r = SIZE * 0.22;
                    const cx = SIZE - r - 2;
                    const cy = r + 2;
                    ctx.beginPath();
                    ctx.arc(cx, cy, r, 0, Math.PI * 2);
                    if (dot) ctx.fillStyle = dot;
                    ctx.fill();
                    if (ring) { ctx.lineWidth = 3; ctx.strokeStyle = ring; ctx.stroke(); }
                    dotted = canvas.toDataURL('image/png');
                } catch { dotted = null; }
                resolve(dotted);
            };
            img.onerror = () => resolve(null);
            img.src = ICON_SRC;
        });
        return drawing;
    }

    function apply() {
        if (want && dotted) {
            link.setAttribute('type', 'image/png');
            link.setAttribute('href', dotted);
        } else if (link.getAttribute('href') !== original.href) {
            if (original.type) link.setAttribute('type', original.type);
            else link.removeAttribute('type');
            link.setAttribute('href', original.href);
        }
    }

    return {
        update(showDot) {
            want = !!showDot;
            if (want && !dotted) draw().then(apply);
            else apply();
        },
    };
}
