// Design foundation contracts: the markup uses component classes, icon-only
// buttons are named and titled, and a few layout fixes stay fixed.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';

const read = (f) => readFileSync(fileURLToPath(new URL(f, import.meta.url)), 'utf8');
const html = read('./index.html');
const css = read('./style.css');
const main = read('./main.js');

describe('design foundation', () => {
  it('index.html sets no look inline (only the display:none the scripts toggle)', () => {
    const styles = [...html.matchAll(/\sstyle="([^"]*)"/g)].map((m) => m[1]);
    expect(styles.filter((s) => !/^\s*display:\s*none;?\s*$/.test(s))).toEqual([]);
  });

  it('the inline-style and type-scale gates pass', () => {
    for (const s of ['check_inline_styles.mjs', 'check_type.mjs']) {
      execFileSync(process.execPath, [fileURLToPath(new URL(`./scripts/${s}`, import.meta.url))], { stdio: 'pipe' });
    }
  });

  it('every icon-only button has an accessible name and a tooltip', () => {
    const missing = [];
    for (const m of html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)) {
      const [, attrs, inner] = m;
      const text = inner.replace(/<svg[\s\S]*?<\/svg>/g, '').replace(/<[^>]+>/g, '').trim();
      if (!/<svg/.test(inner) || text) continue;
      if (!/aria-label=/.test(attrs) || !/\stitle=/.test(attrs)) missing.push(attrs.trim().slice(0, 60));
    }
    expect(missing).toEqual([]);
  });

  it('an empty reactions row takes no space under each message', () => {
    expect(css).toMatch(/\.reactions:empty\s*\{\s*display:\s*none;/);
  });

  it('presence dots go grey while this client is offline', () => {
    expect(css).toMatch(/:root:has\(#self-presence \.dot\.offline\)[^{]*\.avatar-presence[^{]*\{\s*background:\s*var\(--slate-500\)/);
  });

  it('the members panel floats over the feed on a narrow desktop window', () => {
    expect(css).toMatch(/@media \(min-width: 769px\) and \(max-width: 1100px\)\s*\{[\s\S]*?#members-panel\s*\{[^}]*position:\s*absolute/);
  });

  it('relay status badges sit in the message text, not loose in the row', () => {
    expect(main).toMatch(/function _statusHost\(msgEl\)/);
    expect(main).not.toMatch(/\b(el|msgEl)\.appendChild\((badge|b)\);/);
  });
});
