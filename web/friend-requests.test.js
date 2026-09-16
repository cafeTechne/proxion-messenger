import { describe, it, expect, beforeEach } from 'vitest';
import { createFriendRequests } from './friend-requests.js';

let els, sent, socket;
function mkListEl() {
  const children = [];
  return {
    style: {}, _children: children,
    get children() { return children; },
    appendChild(c) { children.push(c); },
  };
}
function mkLi() {
  return { id: '', dataset: {}, style: { cssText: '' }, innerHTML: '' };
}
beforeEach(() => {
  els = {};
  sent = [];
  socket = { readyState: 1, send: (m) => sent.push(JSON.parse(m)) };
  global.WebSocket = { OPEN: 1 };
  global.document = {
    getElementById: (id) => (id in els ? els[id] : null),
    createElement: () => mkLi(),
  };
});

function make() {
  return createFriendRequests({ getSocket: () => socket });
}

describe('renderPendingInvite', () => {
  it('appends an invite row and reveals the section', () => {
    els['friend-request-list'] = mkListEl();
    els['friend-requests-section'] = { style: { display: 'none' } };
    make().renderPendingInvite({ invitation_id: 'inv1', from_did: 'did:key:zAliceLongId', display_name: 'Alice' });
    expect(els['friend-request-list'].children).toHaveLength(1);
    expect(els['friend-request-list'].children[0].innerHTML).toContain('Alice');
    expect(els['friend-requests-section'].style.display).toBe('');
  });
  it('surfaces the stable from_did and marks the name as an unverified claim', () => {
    els['friend-request-list'] = mkListEl();
    make().renderPendingInvite({ invitation_id: 'inv2', from_did: 'did:key:zAliceLongId', display_name: 'Alice' });
    const html = els['friend-request-list'].children[0].innerHTML;
    // The identifier the accept actually binds to is shown inline, not only in title=.
    expect(html).toContain('did:key:zAliceLongId');
    // The peer-chosen name is flagged as unverified so it is not mistaken for identity.
    expect(html.toLowerCase()).toContain('unverified');
  });
  it('two requesters sharing a display name are distinguishable by from_did', () => {
    els['friend-request-list'] = mkListEl();
    const fr = make();
    fr.renderPendingInvite({ invitation_id: 'a', from_did: 'did:key:zRealAlice', display_name: 'Alice' });
    fr.renderPendingInvite({ invitation_id: 'b', from_did: 'did:key:zFakeAlice', display_name: 'Alice' });
    const [rowA, rowB] = els['friend-request-list'].children;
    expect(rowA.innerHTML).toContain('did:key:zRealAlice');
    expect(rowB.innerHTML).toContain('did:key:zFakeAlice');
    expect(rowA.innerHTML).not.toBe(rowB.innerHTML);
  });
  it('is idempotent — a duplicate invitation_id is not re-added', () => {
    els['friend-request-list'] = mkListEl();
    const fr = make();
    fr.renderPendingInvite({ invitation_id: 'inv1', from_did: 'did:key:zA' });
    // second call: getElementById('fri-inv1') must now resolve
    els['fri-inv1'] = els['friend-request-list'].children[0];
    fr.renderPendingInvite({ invitation_id: 'inv1', from_did: 'did:key:zA' });
    expect(els['friend-request-list'].children).toHaveLength(1);
  });
});

describe('acceptFriendRequest', () => {
  it('sends accept_friend_request over an open socket', () => {
    make().acceptFriendRequest('inv9');
    expect(sent).toEqual([{ cmd: 'accept_friend_request', invitation_id: 'inv9' }]);
  });
  it('does nothing when the socket is closed', () => {
    socket.readyState = 3;
    make().acceptFriendRequest('inv9');
    expect(sent).toHaveLength(0);
  });
});

describe('refreshFriendRequestsBadge', () => {
  it('hides the section when the list is empty', () => {
    els['friend-request-list'] = mkListEl();
    els['friend-requests-section'] = { style: { display: '' } };
    make().refreshFriendRequestsBadge();
    expect(els['friend-requests-section'].style.display).toBe('none');
  });
});
