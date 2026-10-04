// Pure, dependency-free helpers extracted from main.js (R40).
// No DOM access, no shared mutable state — safe to unit-test in isolation.
//
// (H) Locale-aware date/number formatting threads the active locale from
// i18n.js. This import is cycle-free: i18n.js imports nothing from the app.
import { getLocale, t } from './i18n.js';

export function didSuffix(id) {
    if (!id || id.length < 5) return "";
    return id.slice(-5);
}

export function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Strip anything outside the base64 alphabet. Wire-supplied base64 (peer
// avatars, voice notes) is interpolated into `src="data:...;base64,${x}"`
// inside innerHTML strings; a stray double-quote there would break out of the
// attribute and inject markup, so any such value MUST pass through here first.
export function b64attr(x) {
    return String(x == null ? '' : x).replace(/[^A-Za-z0-9+/=]/g, '');
}

// Scroll behavior that honours prefers-reduced-motion. The CSS guard does not
// reach scrollIntoView/scrollTo calls that pass behavior: 'smooth' from script.
export function scrollBehavior() {
    try {
        if (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches) return 'auto';
    } catch { /* no matchMedia */ }
    return 'smooth';
}

export function formatTimestamp(ts) {
    if (!ts) return '';
    const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
    if (isNaN(d)) return String(ts);
    return d.toLocaleString(getLocale());
}

export function webidColor(webid) {
    let hash = 0;
    for (let i = 0; i < (webid || "").length; i++)
        hash = (Math.imul(hash, 31) + webid.charCodeAt(i)) | 0;
    const hue = Math.abs(hash) % 360;
    // 68% lightness so the darkest hue (blue) still meets WCAG 4.5:1 on the dark
    // message feed — see scripts/contrast_audit.mjs (worst hue ≈ 4.9:1).
    return `hsl(${hue}, 55%, 68%)`;
}

function _escText(str) {
    return str.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// Escaped URL for use as both href and link text. "@" and ":" become numeric
// references (the browser decodes them back, so the URL is unchanged) so the
// later @mention and :emoji: passes cannot rewrite text inside the attribute.
function _escUrl(url) {
    return escHtml(url).replace(/@/g, "&#64;").replace(/:/g, "&#58;");
}

// Trim trailing sentence punctuation off an autolinked URL. A closing paren is
// kept only when it balances an opening one inside the URL.
function _trimUrl(url) {
    let u = url;
    for (;;) {
        const c = u[u.length - 1];
        if (".,;:!?".includes(c)) { u = u.slice(0, -1); continue; }
        if (c === ")" && u.split("(").length < u.split(")").length) { u = u.slice(0, -1); continue; }
        return u;
    }
}

// Lightweight Markdown renderer (no external deps). Message text is untrusted:
// code spans and URLs are pulled out of the raw text into placeholders first
// (each escaped on its own), the rest is escaped, formatting then runs on the
// escaped text only, and the placeholders are restored last.
export function renderMarkdown(text) {
    if (!text) return "";
    const tokens = [];
    const hold = (html) => `${tokens.push(html) - 1}`;
    let s = String(text).replace(/[]/g, "");
    // Code blocks
    s = s.replace(/```([\s\S]*?)```/g, (_, code) =>
        hold(`<pre class="code-block"><code>${_escText(code.trim())}</code></pre>`));
    // Inline code
    s = s.replace(/`([^`\n]+)`/g, (_, code) => hold(`<code class="inline-code">${_escText(code)}</code>`));
    // Autolink http(s) URLs. The pattern only admits these two schemes and
    // stops at whitespace, quotes, angle brackets and backticks.
    s = s.replace(/(?<![\w@.\/])https?:\/\/[^\s<>"'`]+/gi, (m) => {
        const url = _trimUrl(m);
        if (!/^https?:\/\/[^/?#]/i.test(url)) return m;
        const e = _escUrl(url);
        return hold(`<a href="${e}" rel="noopener noreferrer" target="_blank">${e}</a>`) + m.slice(url.length);
    });
    s = _escText(s);
    // Bold
    s = s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    s = s.replace(/(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)/g, '<b>$1</b>');
    // Italic (underscores only at word boundaries, so snake_case is left alone)
    s = s.replace(/\*([^*\n]+)\*/g, '<i>$1</i>');
    s = s.replace(/(?<!\w)_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)/g, '<i>$1</i>');
    // Strikethrough
    s = s.replace(/~~(.+?)~~/g, '<s>$1</s>');
    // R59D: spoilers — ||text|| hidden until activated (click/Enter/Space via
    // the feed's delegated handler). No nesting; content is already escaped.
    s = s.replace(/\|\|([^|\n]+)\|\|/g, (_, inner) =>
        `<span class="spoiler" role="button" tabindex="0" aria-label="${t('msg.spoilerReveal')}">${inner}</span>`);
    // Newlines (code blocks are still placeholders here, so they keep theirs)
    s = s.replace(/\n/g, '<br>');
    return s.replace(/(\d+)/g, (_, i) => tokens[Number(i)]);
}

export function expireLabel(msRemaining) {
    if (msRemaining <= 0) return "expired";
    const s = Math.floor(msRemaining / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h`;
    return `${Math.floor(h / 24)}d`;
}

export function timeAgo(date) {
    const seconds = Math.floor((new Date() - new Date(date)) / 1000);
    // RelativeTimeFormat has no sub-minute idiom in every locale, so "just now"
    // is a translated string of its own.
    if (seconds < 60) return t('time.justNow');
    const locale = getLocale();
    const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'always', style: 'narrow' });
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return rtf.format(-minutes, 'minute');
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return rtf.format(-hours, 'hour');
    return new Date(date).toLocaleDateString(locale);
}

// Uint8Array <-> base64 (used by chunked file transfer)
export function u8ToB64(u8) {
    let s = "";
    for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
    return btoa(s);
}

export function b64ToU8(b64) {
    const bin = atob(b64 || "");
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return u8;
}
