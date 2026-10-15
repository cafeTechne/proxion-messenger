import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// main.js is the composition root and cannot be imported in the node test env,
// so these guards read its source (same approach as main-empty-states.test.js).
// The live behaviour is covered by smoke_rooms.mjs.
const src = readFileSync(fileURLToPath(new URL('./main.js', import.meta.url)), 'utf8');

const caseBody = (name) => {
  const start = src.indexOf(`case "${name}":`);
  expect(start).toBeGreaterThan(-1);
  const rest = src.slice(start + 1);
  return rest.slice(0, rest.search(/\n {16}case "/));
};

describe('room member list stays current', () => {
  it('re-requests members when someone joins the open room', () => {
    expect(caseBody('room_member_joined')).toContain('requestRoomMembers(event.room_id)');
  });
  it('handles room_member_left by re-requesting members', () => {
    const body = caseBody('room_member_left');
    expect(body).toContain('requestRoomMembers(event.room_id)');
    expect(body).toContain('memberWebIds?.delete(event.webid)');
  });
});

describe('a room that goes away leaves no stale header', () => {
  const fn = src.slice(src.indexOf('function _showThreadClosed('));
  const body = fn.slice(0, fn.indexOf('\n        }\n'));
  it('resets the title and hides the room controls', () => {
    expect(body).toContain("t('heading.welcome')");
    for (const id of ['invite-btn', 'members-toggle', 'integrations-btn', 'leave-room-btn', 'delete-room-btn']) {
      expect(body).toContain(`"${id}"`);
    }
    expect(body).toContain('"room-opt-disappear"');
    expect(body).toContain('_syncRoomOptionsBtn()');
  });
  it('re-syncs owner-only items after an ownership transfer', () => {
    expect(caseBody('ownership_transferred')).toContain('_syncDisappearOption()');
  });
});
