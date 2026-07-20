import { describe, it, expect } from 'vitest';
import {
  didSuffix, escHtml, formatTimestamp, webidColor, renderMarkdown,
  expireLabel, timeAgo, u8ToB64, b64ToU8,
} from './util.js';

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
    expect(webidColor('x')).toMatch(/^hsl\(\d+, 55%, 68%\)$/);
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
