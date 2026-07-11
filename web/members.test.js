import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createMembers } from './members.js';

let els, view, asked;
function mkEl(over = {}) {
  return { style: {}, innerHTML: '', classList: { _c: new Set(), contains(c) { return this._c.has(c); }, toggle(c, on) { on ? this._c.add(c) : this._c.delete(c); } }, ...over };
}
beforeEach(() => {
  els = {};
  view = { type: 'room', id: 'room-1' };
  asked = [];
  global.document = { getElementById: (id) => (id in els ? els[id] : null) };
  global.window = { innerWidth: 1200 };
});

function make() {
  return createMembers({ getActiveView: () => view, requestRoomMembers: (id) => asked.push(id) });
}

describe('toggleMembersPanel (desktop)', () => {
  it('opens a hidden panel and requests the roster', () => {
    els['members-panel'] = mkEl({ style: { display: 'none' } });
    make().toggleMembersPanel();
    expect(els['members-panel'].style.display).toBe('block');
    expect(asked).toEqual(['room-1']);
  });
  it('closes an open panel without re-requesting', () => {
    els['members-panel'] = mkEl({ style: { display: 'block' } });
    make().toggleMembersPanel();
    expect(els['members-panel'].style.display).toBe('none');
    expect(asked).toHaveLength(0);
  });
});

describe('toggleMembersPanel (mobile)', () => {
  it('toggles the mobile-open class and requests on open', () => {
    global.window = { innerWidth: 500 };
    els['members-panel'] = mkEl();
    make().toggleMembersPanel();
    expect(els['members-panel'].classList.contains('mobile-open')).toBe(true);
    expect(asked).toEqual(['room-1']);
  });
});

describe('renderMembersPanel', () => {
  it('groups online and offline members with section headers', () => {
    els['members-list'] = mkEl();
    make().renderMembersPanel([
      { webid: 'did:key:zA', display_name: 'Alice', status: 'online' },
      { webid: 'did:key:zB', display_name: 'Bob', status: 'offline' },
    ]);
    const html = els['members-list'].innerHTML;
    expect(html).toContain('members.online');
    expect(html).toContain('members.offline');
    expect(html).toContain('Alice');
    expect(html).toContain('Bob');
  });
  it('renders a federated badge for remote members', () => {
    els['members-list'] = mkEl();
    make().renderMembersPanel([
      { webid: 'did:key:zC', display_name: 'Carol', status: 'online', federated: true, gateway: 'gw.remote' },
    ]);
    expect(els['members-list'].innerHTML).toContain('Federated member');
  });
});
