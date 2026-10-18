// A disappearing timer counts down only the messages sent after it was turned
// on. Rendering stamped every message with timestamp + timer, so after a
// reload the older messages expired on screen at once and the feed went empty.
import { describe, it, expect } from 'vitest';
import { messageExpiresAt } from './rendering.js';

describe('messageExpiresAt', () => {
  const since = '2026-10-18T10:00:00.000000+00:00';

  it('no timer, no expiry', () => {
    expect(messageExpiresAt('2026-10-18T10:00:05Z', 0, since)).toBeNull();
  });

  it('a message sent before the timer was turned on never expires', () => {
    expect(messageExpiresAt('2026-10-18T09:59:00Z', 30000, since)).toBeNull();
  });

  it('a message sent after it expires timer ms after it was sent', () => {
    expect(messageExpiresAt('2026-10-18T10:00:05Z', 30000, since))
      .toBe(Date.parse('2026-10-18T10:00:35Z'));
  });

  it('with no recorded start (older gateway) the timer applies to every message', () => {
    expect(messageExpiresAt('2026-10-18T09:59:00Z', 30000, ''))
      .toBe(Date.parse('2026-10-18T09:59:30Z'));
  });
});
