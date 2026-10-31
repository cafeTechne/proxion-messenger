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
    expect(make().acceptFriendRequest('inv9')).toBe(false);
    expect(sent).toHaveLength(0);
  });
  it('sends once while the Accept button is in flight', () => {
    const btn = { disabled: false };
    const fr = make();
    expect(fr.acceptFriendRequest('inv9', btn)).toBe(true);
    expect(btn.disabled).toBe(true);
    expect(fr.acceptFriendRequest('inv9', btn)).toBe(false);
    expect(sent).toHaveLength(1);
  });
  it('leaves the button usable when nothing was sent', () => {
    socket.readyState = 3;
    const btn = { disabled: false };
    make().acceptFriendRequest('inv9', btn);
    expect(btn.disabled).toBe(false);
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

describe('sent requests', () => {
  // A list whose children can be found by id and removed, like the DOM.
  function mkSentDom() {
    const list = mkListEl();
    els['sent-request-list'] = list;
    els['sent-requests-section'] = { style: { display: 'none' } };
    global.document.getElementById = (id) => {
      if (id in els) return els[id];
      return list._children.find(c => c.id === id) || null;
    };
    global.document.createElement = () => {
      const li = mkLi();
      li.remove = () => { const i = list._children.indexOf(li); if (i >= 0) list._children.splice(i, 1); };
      return li;
    };
    return list;
  }

  it('lists a sent request with a Cancel button and shows the section', () => {
    const list = mkSentDom();
    make().renderSentRequest({ invitation_id: 's1', target_did: 'did:key:zBob', queued: false });
    expect(list.children).toHaveLength(1);
    const html = list.children[0].innerHTML;
    expect(list.children[0].id).toBe('frs-s1');
    expect(html).toContain('did:key:zBob');
    expect(html).toContain('data-fr-action="withdraw"');
    expect(html).toContain('data-inv-id="s1"');
    expect(html).toContain('fr.waitingForAnswer');
    expect(els['sent-requests-section'].style.display).toBe('');
  });

  it('shows a queued request as waiting to deliver until it is delivered', () => {
    const list = mkSentDom();
    const fr = make();
    fr.renderSentRequest({ invitation_id: 's1', target_did: 'did:key:zBob', queued: true });
    expect(list.children[0].innerHTML).toContain('fr.waitingToDeliver');
    fr.markSentDelivered('s1');
    expect(list.children).toHaveLength(1);
    expect(list.children[0].innerHTML).toContain('fr.waitingForAnswer');
    expect(list.children[0].innerHTML).toContain('did:key:zBob');
  });

  it('a delivery notice for an unknown request adds nothing', () => {
    const list = mkSentDom();
    make().markSentDelivered('nope');
    expect(list.children).toHaveLength(0);
  });

  it('escapes the target name and did', () => {
    const list = mkSentDom();
    make().renderSentRequest({ invitation_id: 's1', target_did: '<img src=x>', display_name: '<b>x</b>' });
    expect(list.children[0].innerHTML).not.toContain('<img');
    expect(list.children[0].innerHTML).not.toContain('<b>x</b>');
  });

  it('the gateway list replaces what is shown and hides an empty section', () => {
    const list = mkSentDom();
    const fr = make();
    fr.renderSentRequest({ invitation_id: 'old', target_did: 'did:key:zOld' });
    fr.setSentRequests([{ invitation_id: 'new', target_did: 'did:key:zNew', queued: true }]);
    expect(list.children.map(c => c.id)).toEqual(['frs-new']);
    expect(fr.hasSentRequest('old')).toBe(false);
    fr.setSentRequests([]);
    expect(list.children).toHaveLength(0);
    expect(els['sent-requests-section'].style.display).toBe('none');
  });

  it('removing a request takes its card away', () => {
    const list = mkSentDom();
    const fr = make();
    fr.renderSentRequest({ invitation_id: 's1', target_did: 'did:key:zBob' });
    fr.removeSentRequest('s1');
    expect(list.children).toHaveLength(0);
    expect(fr.hasSentRequest('s1')).toBe(false);
  });

  it('Cancel sends withdraw_friend_request once while in flight', () => {
    const btn = { disabled: false };
    const fr = make();
    expect(fr.withdrawFriendRequest('s1', btn)).toBe(true);
    expect(fr.withdrawFriendRequest('s1', btn)).toBe(false);
    expect(btn.disabled).toBe(true);
    expect(sent).toEqual([{ cmd: 'withdraw_friend_request', invitation_id: 's1' }]);
  });

  it('Cancel does nothing on a closed socket', () => {
    socket.readyState = 3;
    const btn = { disabled: false };
    expect(make().withdrawFriendRequest('s1', btn)).toBe(false);
    expect(btn.disabled).toBe(false);
    expect(sent).toHaveLength(0);
  });
});
