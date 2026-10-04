import { describe, it, expect } from 'vitest';
import { ICONS, icon } from './icons.js';

describe('icon()', () => {
  it('returns a decorative Heroicons-outline svg', () => {
    const s = icon('x-mark');
    expect(s).toMatch(/^<svg /);
    expect(s).toContain('viewBox="0 0 24 24"');
    expect(s).toContain('stroke-width="1.5"');
    expect(s).toContain('stroke="currentColor"');
    expect(s).toContain('aria-hidden="true"');
    expect(s).toContain('focusable="false"');
    expect(s).toContain('width="16" height="16"');
    expect(s).toContain(`d="${ICONS['x-mark']}"`);
  });

  it('applies size and extra classes', () => {
    const s = icon('clock', { size: 20, cls: 'big' });
    expect(s).toContain('width="20" height="20"');
    expect(s).toContain('class="icon big"');
  });

  it('marks directional icons so RTL can mirror them', () => {
    expect(icon('reply')).toContain('class="icon icon-dir"');
    expect(icon('forward')).toContain('icon-dir');
    expect(icon('chevron-left')).toContain('icon-dir');
    expect(icon('pin')).not.toContain('icon-dir');
  });

  it('keeps pin and bookmark distinct', () => {
    expect(ICONS.pin).not.toBe(ICONS.bookmark);
  });

  it('returns an empty string for an unknown name', () => {
    expect(icon('nope')).toBe('');
  });
});
