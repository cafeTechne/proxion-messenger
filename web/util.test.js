import { describe, it, expect, afterEach } from 'vitest';
import {
  didSuffix, escHtml, formatTimestamp, webidColor, renderMarkdown,
  expireLabel, timeAgo, u8ToB64, b64ToU8, b64attr, scrollBehavior,
} from './util.js';

describe('scrollBehavior (reduced motion)', () => {
  const orig = globalThis.matchMedia;
  afterEach(() => { globalThis.matchMedia = orig; });
  it('is smooth when the user has no motion preference', () => {
    globalThis.matchMedia = () => ({ matches: false });
    expect(scrollBehavior()).toBe('smooth');
  });
  it('is instant when prefers-reduced-motion is set', () => {
    globalThis.matchMedia = (q) => ({ matches: q === '(prefers-reduced-motion: reduce)' });
    expect(scrollBehavior()).toBe('auto');
  });
  it('falls back to smooth without matchMedia', () => {
    globalThis.matchMedia = undefined;
    expect(scrollBehavior()).toBe('smooth');
  });
});

describe('b64attr (attribute-injection guard)', () => {
  it('keeps a valid base64 string intact', () => {
    expect(b64attr('SGVsbG8gd29ybGQ=')).toBe('SGVsbG8gd29ybGQ=');
    expect(b64attr('a+/9Z=')).toBe('a+/9Z=');
  });
  it('strips characters that could break out of a src attribute', () => {
    // A malicious peer avatar/voice-note payload trying to inject markup.
    // '=' and '/' and '+' are valid base64 and stay; the injection depends on
    // '"', '<', '>' and spaces, which are all stripped, so no breakout remains.
    expect(b64attr('AAAA" onerror="alert(1)')).toBe('AAAAonerror=alert1');
    expect(b64attr('x"><img src=x onerror=alert(1)>')).toBe('ximgsrc=xonerror=alert1');
    expect(b64attr('data" onload="evil')).not.toMatch(/["<>]/);
    expect(b64attr('any" attack')).not.toMatch(/[\s"]/);   // no quote or whitespace survives
  });
  it('handles null/undefined/non-strings', () => {
    expect(b64attr(null)).toBe('');
    expect(b64attr(undefined)).toBe('');
    expect(b64attr(123)).toBe('123');
  });
});

describe('escHtml (XSS guard)', () => {
  it('escapes all dangerous characters', () => {
    expect(escHtml('<script>alert("x")</script>'))
      .toBe('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;');
  });
  it('escapes ampersands and single quotes', () => {
    expect(escHtml(`a & b's`)).toBe('a &amp; b&#39;s');
  });
  it('stringifies non-strings safely', () => {
    expect(escHtml(42)).toBe('42');
    expect(escHtml(null)).toBe('null');
  });
});

describe('didSuffix', () => {
  it('returns last 5 chars', () => {
    expect(didSuffix('did:key:z6MkABCDE')).toBe('ABCDE');
  });
  it('returns empty for short/empty input', () => {
    expect(didSuffix('abcd')).toBe('');
    expect(didSuffix('')).toBe('');
    expect(didSuffix(null)).toBe('');
  });
});

describe('webidColor', () => {
  it('is deterministic for the same input', () => {
    expect(webidColor('did:key:zAlice')).toBe(webidColor('did:key:zAlice'));
  });
  it('returns an hsl string', () => {
    expect(webidColor('x')).toMatch(/^hsl\(\d+, 55%, var\(--webid-l, 68%\)\)$/);
  });
  it('handles empty/undefined without throwing', () => {
    expect(webidColor('')).toMatch(/^hsl\(/);
    expect(webidColor(undefined)).toMatch(/^hsl\(/);
  });
});

describe('renderMarkdown', () => {
  it('escapes HTML before formatting (no injection)', () => {
    expect(renderMarkdown('<b>x</b>')).toBe('&lt;b&gt;x&lt;/b&gt;');
  });
  it('renders bold, italic, strikethrough, inline code', () => {
    expect(renderMarkdown('**b**')).toBe('<b>b</b>');
    expect(renderMarkdown('*i*')).toBe('<i>i</i>');
    expect(renderMarkdown('~~s~~')).toBe('<s>s</s>');
    expect(renderMarkdown('`c`')).toBe('<code class="inline-code">c</code>');
  });
  it('converts newlines to <br>', () => {
    expect(renderMarkdown('a\nb')).toBe('a<br>b');
  });
  it('returns empty string for falsy input', () => {
    expect(renderMarkdown('')).toBe('');
  });
});

describe('expireLabel', () => {
  it('formats durations', () => {
    expect(expireLabel(0)).toBe('expired');
    expect(expireLabel(30_000)).toBe('30s');
    expect(expireLabel(120_000)).toBe('2m');
    expect(expireLabel(3 * 3600_000)).toBe('3h');
    expect(expireLabel(2 * 24 * 3600_000)).toBe('2d');
  });
});

describe('timeAgo', () => {
  it('returns "Just now" for the present', () => {
    expect(timeAgo(new Date())).toBe('time.justNow');
  });
  it('returns minutes for a few minutes ago', () => {
    expect(timeAgo(new Date(Date.now() - 5 * 60_000))).toBe('5m ago');
  });
});

describe('base64 <-> Uint8Array', () => {
  it('round-trips arbitrary bytes', () => {
    const bytes = new Uint8Array([0, 1, 2, 250, 255, 127, 128]);
    expect(Array.from(b64ToU8(u8ToB64(bytes)))).toEqual(Array.from(bytes));
  });
  it('b64ToU8 handles empty input', () => {
    expect(b64ToU8('').length).toBe(0);
    expect(b64ToU8(null).length).toBe(0);
  });
});

describe('formatTimestamp', () => {
  it('returns empty for falsy', () => {
    expect(formatTimestamp(0)).toBe('');
    expect(formatTimestamp('')).toBe('');
  });
  it('passes through unparseable as string', () => {
    expect(formatTimestamp('not-a-date')).toBe('not-a-date');
  });
});

describe('renderMarkdown spoilers (R59D)', () => {
  it('wraps ||text|| in an activatable spoiler span', async () => {
    const { renderMarkdown } = await import('./util.js');
    const html = renderMarkdown('the killer is ||the butler||!');
    expect(html).toContain('class="spoiler"');
    expect(html).toContain('role="button"');
    expect(html).toContain('tabindex="0"');
    expect(html).toContain('the butler');
    expect(html.startsWith('the killer is ')).toBe(true);
  });
  it('keeps markup inside spoilers escaped', async () => {
    const { renderMarkdown } = await import('./util.js');
    const html = renderMarkdown('||<img src=x onerror=alert(1)>||');
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
  });
  it('does not match single pipes or spanning newlines', async () => {
    const { renderMarkdown } = await import('./util.js');
    expect(renderMarkdown('a | b | c')).not.toContain('spoiler');
    expect(renderMarkdown('||a\nb||')).not.toContain('class="spoiler"');
  });
  it('supports multiple spoilers in one message', async () => {
    const { renderMarkdown } = await import('./util.js');
    const html = renderMarkdown('||one|| and ||two||');
    expect(html.match(/class="spoiler"/g)).toHaveLength(2);
  });
});

describe('renderMarkdown code spans, autolinks and underscores', () => {
  const A = (href) => `<a href="${href}" rel="noopener noreferrer" target="_blank">${href}</a>`;
  it('leaves formatting markers inside inline code alone', () => {
    expect(renderMarkdown('`my_var_name` and `**x**`'))
      .toBe('<code class="inline-code">my_var_name</code> and <code class="inline-code">**x**</code>');
  });
  it('keeps newlines inside fenced code blocks', () => {
    expect(renderMarkdown('```\na_b_c\nx\n```')).toBe('<pre class="code-block"><code>a_b_c\nx</code></pre>');
  });
  it('does not italicise snake_case text', () => {
    expect(renderMarkdown('call my_var_name now')).toBe('call my_var_name now');
    expect(renderMarkdown('a __init__ method')).toBe('a <b>init</b> method');
    expect(renderMarkdown('foo__bar__baz')).toBe('foo__bar__baz');
    expect(renderMarkdown('_hi_ there')).toBe('<i>hi</i> there');
  });
  it('autolinks http and https URLs', () => {
    expect(renderMarkdown('see https://example.com/a')).toBe('see ' + A('https&#58;//example.com/a'));
    expect(renderMarkdown('http://x.org')).toBe(A('http&#58;//x.org'));
  });
  it('does not swallow trailing punctuation', () => {
    expect(renderMarkdown('go to https://x.com/a.')).toBe('go to ' + A('https&#58;//x.com/a') + '.');
    expect(renderMarkdown('(https://x.com/a), ok;')).toBe('(' + A('https&#58;//x.com/a') + '), ok;');
    expect(renderMarkdown('https://en.wikipedia.org/wiki/Foo_(bar)'))
      .toBe(A('https&#58;//en.wikipedia.org/wiki/Foo_(bar)'));
  });
  it('keeps underscores in URLs intact', () => {
    expect(renderMarkdown('https://x.com/some_long_path_name'))
      .toBe(A('https&#58;//x.com/some_long_path_name'));
  });
  it('does not link a URL inside code', () => {
    expect(renderMarkdown('`https://x.com`')).toBe('<code class="inline-code">https://x.com</code>');
  });
  it('never links a javascript: or other non-http scheme', () => {
    for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<b>x</b>', 'vbscript:x']) {
      expect(renderMarkdown(bad)).not.toContain('<a ');
    }
    expect(renderMarkdown('javascript:https://x.com')).not.toMatch(/href="javascript/i);
  });
  it('cannot break out of the href attribute', () => {
    expect(renderMarkdown('https://x.com/"onmouseover=alert(1)'))
      .toBe(A('https&#58;//x.com/') + '"onmouseover=alert(1)');
    const html2 = renderMarkdown("https://x.com/'onmouseover=alert(1) https://x.com/<img/src=x>");
    expect(html2).not.toMatch(/<a [^>]*onmouseover/);
    expect(html2).not.toContain('<img');
    expect(renderMarkdown('https://x.com/&quot;onmouseover=alert(1)'))
      .toContain('href="https&#58;//x.com/&amp;quot;onmouseover=alert(1)"');
  });
  it('hides @ and : in links from the mention and emoji passes', () => {
    const html = renderMarkdown('https://x.com/@bob/:smile:');
    expect(html).not.toMatch(/@\w/);
    expect(html).not.toMatch(/:[a-z0-9_]{2,32}:/);
  });
  it('strips placeholder sentinels from input', () => {
    expect(renderMarkdown('0 `x`')).toBe('0 <code class="inline-code">x</code>');
  });
});
