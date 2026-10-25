// Pure, dependency-free helpers extracted from main.js (R40).
// No DOM access, no shared mutable state — safe to unit-test in isolation.
//
// (H) Locale-aware date/number formatting threads the active locale from
// i18n.js. This import is cycle-free: i18n.js imports nothing from the app.
import { getLocale, t } from './i18n.js';

// Where the gateway's HTTP endpoints live. A page the gateway served posts
// same-origin. The desktop webview is served from the Tauri asset origin
// (tauri://localhost, or https://tauri.localhost on Windows), where a relative
// URL resolves to the bundled files, so it goes to the sidecar's fixed loopback
// port instead (PROXION_HTTP_PORT in tauri-app/src-tauri/src/main.rs).
export const DESKTOP_GATEWAY_HTTP = 'http://127.0.0.1:8080';

const _pageLocation = () => globalThis.window?.location || globalThis.location;

export function isTauriAssetOrigin(loc = _pageLocation()) {
    return !!loc && (loc.protocol === 'tauri:' || loc.hostname === 'tauri.localhost');
}

export function gatewayHttpBase(loc = _pageLocation()) {
    if (!loc) return '';
    if (isTauriAssetOrigin(loc) || !/^https?:$/.test(loc.protocol || '')) return DESKTOP_GATEWAY_HTTP;
    return '';
}

// Absolute (desktop) or same-origin URL for a gateway path such as '/health'.
export function gatewayUrl(path, loc = _pageLocation()) {
    return gatewayHttpBase(loc) + path;
}

// Inline id shown beside a peer-chosen display name in member, contact and DM
// rows, so a copied name cannot pass for someone else. Ten base58 characters
// (~58 bits) from the end: the start of every did:key is the same "z6Mk", and
// the 5-character message-header suffix is too short to resist a ground key.
// It ends with that suffix, so a row still visibly matches its messages.
export function didIdTag(id) {
    if (!id || id.length < 10) return "";
    return id.slice(-10);
}

// Label for a DM whose peer has not told us a name yet. "did:key:z6Mk…" says
// nothing (every did:key starts that way), so show the distinguishing tail.
export function dmFallbackName(id) {
    if (!id) return "";
    if (id.startsWith("did:key:") && didIdTag(id)) return "did:key:…" + didIdTag(id);
    return id.length > 24 ? id.slice(0, 23) + "…" : id;
}

// Plain-text "Name ·idtag" for surfaces that show who someone is outside a
// row (call banner, call widget, voice tiles): the same ten-character tag the
// member, contact and DM rows use, so one person reads the same everywhere.
// Without a name, falls back to dmFallbackName (never a raw did:key prefix).
export function nameWithIdTag(name, id) {
    const tag = didIdTag(id || "");
    if (!name) return dmFallbackName(id || "");
    return tag ? `${name} ·${tag}` : name;
}

export function didSuffix(id) {
    if (!id || id.length < 5) return "";
    return id.slice(-5);
}

// A presence status as words ("online", "away"), translated. Unknown or empty
// statuses give "" so a missing status never shows a raw wire value.
const _PRESENCE_KEYS = { online: 'presence.online', away: 'presence.away', busy: 'presence.busy', offline: 'presence.offline' };
export function presenceLabel(status) {
    return Object.prototype.hasOwnProperty.call(_PRESENCE_KEYS, status) ? t(_PRESENCE_KEYS[status]) : '';
}

export function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// First letter or digit of a name for a letter avatar. Skips leading punctuation,
// symbols and emoji ("…Alice" gives "A") and keeps surrogate pairs whole.
export function avatarInitial(name) {
    const m = String(name || '').match(/[\p{L}\p{N}]/u);
    return m ? m[0].toLocaleUpperCase() : '?';
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

// True when the primary pointer is a finger (phones, tablets). Hybrid laptops
// with a touch screen report their trackpad, so they count as desktop.
export function isCoarsePointer() {
    try {
        return typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;
    } catch { return false; }
}

// True when CSS can size a textarea to its content (field-sizing: content),
// so the scrollHeight resize fallbacks can stand down.
export function supportsFieldSizing() {
    try {
        return typeof CSS !== 'undefined' && typeof CSS.supports === 'function'
            && CSS.supports('field-sizing', 'content');
    } catch { return false; }
}

// The platform share sheet (Web Share API) exists here: phones, and some
// desktops. Where it does not, Proxion only offers Copy.
export function canShareLinks(nav = globalThis.navigator) {
    return !!(nav && typeof nav.share === 'function');
}

// Open the share sheet for a link. Resolves to 'shared', 'cancelled' (the user
// closed the sheet, nothing to do), 'unsupported' or 'failed' (the caller
// falls back to copying).
export async function shareLink(url, { title, text } = {}, nav = globalThis.navigator) {
    const data = { url };
    if (title) data.title = title;
    if (text) data.text = text;
    if (!canShareLinks(nav)) return 'unsupported';
    try {
        if (typeof nav.canShare === 'function' && !nav.canShare(data)) return 'unsupported';
        await nav.share(data);
        return 'shared';
    } catch (e) {
        return e && e.name === 'AbortError' ? 'cancelled' : 'failed';
    }
}

// What Enter does in the message composer: 'send', 'newline' (let the
// textarea break the line) or 'none' (not ours to handle). Desktop: Enter
// sends, Shift+Enter breaks the line. Touch: Enter breaks the line and the
// Send button sends, as in most mobile chat apps; Ctrl/Cmd+Enter still sends
// for a tablet with a keyboard. An Enter that confirms an IME candidate (CJK
// input) is never a send.
export function composerEnterAction(e, coarse) {
    if (!e || e.key !== 'Enter') return 'none';
    if (e.isComposing || e.keyCode === 229) return 'none';
    if (e.shiftKey) return 'newline';
    if (coarse && !e.ctrlKey && !e.metaKey) return 'newline';
    return 'send';
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
    // Lightness comes from --webid-l in style.css (68% dark, 30% light) so the
    // worst hue still meets WCAG 4.5:1 on the feed in each theme; checked by
    // scripts/contrast_audit.mjs. The fallback keeps the dark value.
    return `hsl(${hue}, 55%, var(--webid-l, 68%))`;
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
// Inline formatting on one escaped line: bold, italic, strikethrough, spoilers.
function _inlineMarkdown(s) {
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
    return s.replace(/\|\|([^|\n]+)\|\|/g, (_, inner) =>
        `<span class="spoiler" role="button" tabindex="0" aria-label="${t('msg.spoilerReveal')}">${inner}</span>`);
}

export function renderMarkdown(text) {
    if (!text) return "";
    const tokens = [];
    const hold = (html) => `${tokens.push(html) - 1}`;
    let s = String(text).replace(/[]/g, "");
    // Code blocks. A language tag on the opening fence (```js) is dropped:
    // there is no highlighter, and it is not part of the code.
    s = s.replace(/```(?:[\w+#.-]+(?=\n))?([\s\S]*?)```/g, (_, code) =>
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
    // Block structure, one level deep: "> " quotes and "- ", "* ", "1. " lists.
    // Other lines keep their breaks (code blocks are still placeholders here,
    // so they keep theirs).
    const groups = [];
    for (const line of s.split('\n')) {
        let kind = null, body = line, start = 1, m;
        if ((m = /^&gt; ?(.*)$/.exec(line))) { kind = 'quote'; body = m[1]; }
        else if ((m = /^[-*] (.*)$/.exec(line))) { kind = 'ul'; body = m[1]; }
        else if ((m = /^(\d{1,9})[.)] (.*)$/.exec(line))) { kind = 'ol'; body = m[2]; start = Number(m[1]); }
        const last = groups[groups.length - 1];
        if (last && last.kind === kind) last.lines.push(body);
        else groups.push({ kind, start, lines: [body] });
    }
    s = groups.map(({ kind, start, lines }) => {
        const inl = lines.map(_inlineMarkdown);
        if (kind === 'quote') return `<blockquote class="md-quote">${inl.join('<br>')}</blockquote>`;
        const items = inl.map(l => `<li>${l}</li>`).join('');
        if (kind === 'ul') return `<ul class="md-list">${items}</ul>`;
        if (kind === 'ol') return `<ol class="md-list"${start !== 1 ? ` start="${start}"` : ''}>${items}</ol>`;
        return inl.join('<br>');
    }).join('');
    return s.replace(/(\d+)/g, (_, i) => tokens[Number(i)]);
}

// One-line plain text for sidebar previews: drops the Markdown markers that
// renderMarkdown would turn into formatting, masks spoilers (a preview must
// not reveal them) and folds line breaks into spaces.
export function markdownToPlain(text) {
    return String(text || "")
        .replace(/```(?:[\w+#.-]+(?=\n))?([\s\S]*?)```/g, (_, code) => code.trim())
        .replace(/`([^`\n]+)`/g, "$1")
        .replace(/\|\|[^|\n]+\|\|/g, "•••")
        .replace(/\*\*(.+?)\*\*/g, "$1")
        .replace(/(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)/g, "$1")
        .replace(/\*([^*\n]+)\*/g, "$1")
        .replace(/(?<!\w)_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)/g, "$1")
        .replace(/~~(.+?)~~/g, "$1")
        .replace(/^(?:>|[-*]|\d{1,9}[.)]) /gm, "")
        .replace(/\s+/g, " ")
        .trim();
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

// Attachment size for display: "18 bytes", "6.2 kB", "2 MB". Rounding everything
// to whole KB showed small files as "0 KB".
export function formatFileSize(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n < 0) return "";
    let unit = "byte", v = n;
    if (n >= 1024 * 1024) { unit = "megabyte"; v = n / (1024 * 1024); }
    else if (n >= 1024) { unit = "kilobyte"; v = n / 1024; }
    const digits = unit === "byte" || v >= 10 ? 0 : 1;
    try {
        return new Intl.NumberFormat(getLocale(), {
            style: "unit", unit, unitDisplay: unit === "byte" ? "long" : "short", maximumFractionDigits: digits,
        }).format(v);
    } catch {
        return `${v.toFixed(digits)} ${unit === "byte" ? "B" : unit === "kilobyte" ? "KB" : "MB"}`;
    }
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
