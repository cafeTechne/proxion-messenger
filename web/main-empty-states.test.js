import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// main.js is the composition root and cannot be imported in the node test env,
// so these guards read its source (same approach as main-selector.test.js).
const src = readFileSync(fileURLToPath(new URL('./main.js', import.meta.url)), 'utf8');
const html = readFileSync(fileURLToPath(new URL('./index.html', import.meta.url)), 'utf8');

describe('settings name readout', () => {
  it('re-renders after the display-name autosave', () => {
    const fn = src.slice(src.indexOf('function _saveDisplayName('));
    const body = fn.slice(0, fn.indexOf('function _saveStatusMessage('));
    expect(body).toContain('_renderSettingsDid();');
  });
});

describe('feed empty states in main.js', () => {
  it('a conversation that goes away renders the shared empty-state helper with actions', () => {
    const fn = src.slice(src.indexOf('function _showThreadClosed('));
    const body = fn.slice(0, fn.indexOf('\n        }\n'));
    expect(body).toContain('feedEmptyState(');
    expect(body).toContain("t('control.createRoom')");
    expect(body).toContain("t('btn.addContact2')");
    for (const key of ['feed.dmHidden', 'feed.removedFromRoom', 'feed.roomDeletedLastMember',
      'feed.leftRoom', 'feed.leftRoomTransferred', 'feed.roomDeletedByOwner']) {
      expect(src).toContain(`t('${key}'`);
    }
    expect((src.match(/_showThreadClosed\(/g) || []).length).toBeGreaterThanOrEqual(5);
  });

  it('no longer writes hardcoded English system lines for leave/remove/delete', () => {
    expect(src).not.toContain('You were removed from this room.');
    expect(src).not.toContain('you were the last member');
    expect(src).not.toContain('was deleted by its owner.</div>');
    expect(src).not.toContain('DM hidden.');
  });

  it('a lone room owner is pointed at the invite action', () => {
    const fn = src.slice(src.indexOf('function _newThreadEmptyState('));
    expect(fn).toContain("t('feed.invitePeople')");
    expect(fn).toContain('copyRoomInvite()');
  });

  it('the contacts-section #empty-state and its helpers are gone', () => {
    expect(html).not.toContain('id="empty-state"');
    expect(src).not.toMatch(/\b(show|hide)EmptyState\b/);
    expect(src).not.toContain('dm.addContactHint');
  });
});
