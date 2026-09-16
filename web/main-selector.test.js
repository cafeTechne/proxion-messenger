import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// main.js is the composition root (a top-level IIFE that touches document/window
// on import), so it cannot be imported into the node test env. These guards read
// its source and assert the message-feed selector lookups escape the client-
// supplied event.message_id, which a `"` would otherwise use to break the
// selector string and throw a SyntaxError that aborts the handler.
const src = readFileSync(fileURLToPath(new URL('./main.js', import.meta.url)), 'utf8');

describe('main.js message-id selector lookups', () => {
  it('wraps every data-message-id selector value in CSS.escape', () => {
    const escaped = src.match(/data-message-id="\$\{CSS\.escape\(event\.message_id\)\}"/g) || [];
    expect(escaped.length).toBe(4);
  });
  it('leaves no raw event.message_id interpolation in a selector', () => {
    expect(src).not.toMatch(/data-message-id="\$\{event\.message_id\}"/);
  });
});

// The WHATWG reference CSS.escape (as shipped by browsers). Mirrored here so the
// test can prove the property the fix relies on without a DOM.
function cssEscape(value) {
  const string = String(value);
  const length = string.length;
  let result = '';
  for (let index = 0; index < length; index++) {
    const codeUnit = string.charCodeAt(index);
    if (codeUnit === 0x0000) { result += '�'; continue; }
    if ((codeUnit >= 0x0001 && codeUnit <= 0x001F) || codeUnit === 0x007F ||
        (index === 0 && codeUnit >= 0x0030 && codeUnit <= 0x0039) ||
        (index === 1 && codeUnit >= 0x0030 && codeUnit <= 0x0039 && string.charCodeAt(0) === 0x002D)) {
      result += '\\' + codeUnit.toString(16) + ' ';
      continue;
    }
    if (index === 0 && length === 1 && codeUnit === 0x002D) { result += '\\' + string.charAt(index); continue; }
    if (codeUnit >= 0x0080 || codeUnit === 0x002D || codeUnit === 0x005F ||
        (codeUnit >= 0x0030 && codeUnit <= 0x0039) ||
        (codeUnit >= 0x0041 && codeUnit <= 0x005A) ||
        (codeUnit >= 0x0061 && codeUnit <= 0x007A)) {
      result += string.charAt(index);
      continue;
    }
    result += '\\' + string.charAt(index);
  }
  return result;
}

describe('main.js DM sidebar surfaces a stable id, not just the peer-chosen name', () => {
  it('renders an inline short id tag when a display name is present', () => {
    // The first inbound DM persists the sender's self-chosen display name as the
    // sidebar identity. That name can copy a trusted contact's, so the row must
    // also show a stable id derived from the webid (not only in a hover title=).
    expect(src).toContain('class="dm-item-id"');
    // The tag is gated on a display name and built from the peer webid/thread id.
    expect(src).toMatch(/idTag = peer\.display_name/);
    expect(src).toMatch(/peer\.peer_webid \|\| id \|\| ""\)\.slice\(8, 22\)/);
    // The dm-item-name line actually interpolates the tag next to the escaped name.
    expect(src).toMatch(/dm-item-name[^`]*\$\{escHtml\(name\)\}\$\{idTag\}/);
  });
});

describe('CSS.escape neutralizes a quote-bearing message id', () => {
  it('escapes the double quote so the built selector has no bare quote to break out on', () => {
    const escaped = cssEscape('"><img src=x onerror=alert(1)>');
    // Every quote in the escaped value is backslash-escaped, so once those are
    // removed none remain to prematurely close the attribute value (the
    // SyntaxError that would abort the handler).
    expect(escaped.replace(/\\"/g, '')).not.toContain('"');
    // A benign id is untouched, so normal lookups keep working.
    expect(`[data-message-id="${cssEscape('m1')}"]`).toBe('[data-message-id="m1"]');
  });
});
