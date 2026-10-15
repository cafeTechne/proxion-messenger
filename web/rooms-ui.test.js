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
