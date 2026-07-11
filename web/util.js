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

// Lightweight Markdown renderer (no external deps). Escapes HTML first.
export function renderMarkdown(text) {
    if (!text) return "";
    let s = text.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    // Code blocks
    s = s.replace(/```([\s\S]*?)```/g, (_, code) =>
        `<pre class="code-block"><code>${code.trim()}</code></pre>`);
    // Inline code
    s = s.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
    // Bold
    s = s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    s = s.replace(/__(.+?)__/g, '<b>$1</b>');
    // Italic
    s = s.replace(/\*([^*\n]+)\*/g, '<i>$1</i>');
    s = s.replace(/_([^_\n]+)_/g, '<i>$1</i>');
    // Strikethrough
    s = s.replace(/~~(.+?)~~/g, '<s>$1</s>');
    // Newlines (not inside pre blocks)
    s = s.replace(/\n/g, '<br>');
    return s;
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
