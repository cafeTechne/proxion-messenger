"""Tests for proxion_messenger_core.replies."""
from __future__ import annotations

import pytest
from proxion_messenger_core.messaging import Message, compose
from proxion_messenger_core.replies import get_replies, build_thread_view
from proxion_messenger_core import AgentState, run_local_handshake
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.store import MemoryStore

@pytest.fixture
def alice():
    return AgentState.generate()

@pytest.fixture
def bob():
    return AgentState.generate()

@pytest.fixture
def cert(alice, bob):
    store = MemoryStore()
    caps = [Capability(can="read", with_="stash://messages/"), Capability(can="write", with_="stash://messages/")]
    cert, _ = run_local_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_capabilities=caps,
        bob_capabilities=caps,
        store=store
    )
    return cert

def test_get_replies_filters_correctly(alice, cert):
    m1 = compose(alice.identity_key, cert, "root1")
    m2 = compose(alice.identity_key, cert, "root2")
    r1 = compose(alice.identity_key, cert, "reply to root1", reply_to_id=m1.message_id)
    r2 = compose(alice.identity_key, cert, "reply to root2", reply_to_id=m2.message_id)
    not_reply = compose(alice.identity_key, cert, "reaction to root1", 
                        reply_to_id=m1.message_id, message_type="reaction")
    
    msgs = [m1, m2, r1, r2, not_reply]
    replies = get_replies(msgs, m1.message_id)
    
    assert len(replies) == 1
    assert replies[0].message_id == r1.message_id
    assert replies[0].content == "reply to root1"

def test_build_thread_view_structure(alice, bob, cert):
    m1 = compose(alice.identity_key, cert, "m1")
    m2 = compose(bob.identity_key, cert, "m2")
    r1 = compose(bob.identity_key, cert, "reply to m1", reply_to_id=m1.message_id)
    r2 = compose(alice.identity_key, cert, "another reply to m1", reply_to_id=m1.message_id)
    
    msgs = [m1, m2, r1, r2]
    view = build_thread_view(msgs)
    
    assert len(view) == 2
    # m1's entry
    root, replies = view[0]
    assert root.message_id == m1.message_id
    assert len(replies) == 2
    assert replies[0].message_id == r1.message_id
    
    # m2's entry
    root2, replies2 = view[1]
    assert root2.message_id == m2.message_id
    assert len(replies2) == 0

def test_build_thread_view_sorting(alice, cert):
    import datetime
    t1 = datetime.datetime(2026, 4, 10, 10, 0, 0, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2026, 4, 10, 11, 0, 0, tzinfo=datetime.timezone.utc)
    
    m1 = compose(alice.identity_key, cert, "oldest", now=t1)
    m2 = compose(alice.identity_key, cert, "newest", now=t2)
    
    msgs = [m2, m1] # unordered input
    view = build_thread_view(msgs)
    
    assert view[0][0].content == "oldest"
    assert view[1][0].content == "newest"

def test_get_replies_invalid_target(alice, cert):
    msgs = [compose(alice.identity_key, cert, "root")]
    replies = get_replies(msgs, "missing-id")
    assert len(replies) == 0


def test_get_thread_builds_tree(alice, cert):
    """Test that get_thread builds a nested tree structure."""
    from proxion_messenger_core.replies import get_thread
    
    m1 = compose(alice.identity_key, cert, "root")
    r1 = compose(alice.identity_key, cert, "reply to root", reply_to_id=m1.message_id)
    r2 = compose(alice.identity_key, cert, "another reply", reply_to_id=m1.message_id)
    r1_1 = compose(alice.identity_key, cert, "reply to r1", reply_to_id=r1.message_id)
    
    messages = [m1, r1, r2, r1_1]
    tree = get_thread(messages, m1.message_id)
    
    # Root should be m1
    assert tree["message"].message_id == m1.message_id
    # Should have 2 replies
    assert len(tree["replies"]) == 2
    # First reply (r1) should have a nested reply
    r1_reply = tree["replies"][0]
    assert r1_reply["message"].message_id == r1.message_id
    assert len(r1_reply["replies"]) == 1
    assert r1_reply["replies"][0]["message"].message_id == r1_1.message_id


def test_get_thread_max_depth(alice, cert):
    """Test that get_thread respects max_depth parameter."""
    from proxion_messenger_core.replies import get_thread
    
    m1 = compose(alice.identity_key, cert, "root")
    current = m1
    # Create a deep chain
    for i in range(15):
        current = compose(alice.identity_key, cert, f"reply {i}", reply_to_id=current.message_id)
    
    messages = [m1]
    # Add all replies
    temp = m1
    for i in range(15):
        temp = compose(alice.identity_key, cert, f"reply {i}", reply_to_id=temp.message_id)
        messages.append(temp)
    
    # With max_depth=5, should stop at depth 5
    tree = get_thread(messages, m1.message_id, max_depth=5)
    
    # Count the depth by traversing
    def count_depth(t):
        if not t.get("replies"):
            return 0
        return 1 + max([count_depth(r) for r in t["replies"]], default=0)
    
    depth = count_depth(tree)
    assert depth <= 5


def test_flatten_thread_depth_order(alice, cert):
    """Test that flatten_thread preserves depth and pre-order DFS."""
    from proxion_messenger_core.replies import get_thread, flatten_thread
    
    m1 = compose(alice.identity_key, cert, "root")
    r1 = compose(alice.identity_key, cert, "reply 1", reply_to_id=m1.message_id)
    r2 = compose(alice.identity_key, cert, "reply 2", reply_to_id=m1.message_id)
    r1_1 = compose(alice.identity_key, cert, "nested", reply_to_id=r1.message_id)
    
    messages = [m1, r1, r2, r1_1]
    tree = get_thread(messages, m1.message_id)
    flattened = flatten_thread(tree)
    
    # Should have 4 messages
    assert len(flattened) == 4
    
    # Check depths
    depths = [t[0] for t in flattened]
    message_ids = [t[1].message_id for t in flattened]
    
    # First should be root at depth 0
    assert flattened[0][0] == 0
    assert flattened[0][1].message_id == m1.message_id
    
    # Two messages at depth 1 (r1 and r2)
    depth_1_items = [(d, mid) for d, mid in zip(depths, message_ids) if d == 1]
    assert len(depth_1_items) == 2
    assert r1.message_id in [mid for _, mid in depth_1_items]
    assert r2.message_id in [mid for _, mid in depth_1_items]
    
    # One message at depth 2 (r1_1) - should be nested under r1
    depth_2_items = [(d, mid) for d, mid in zip(depths, message_ids) if d == 2]
    assert len(depth_2_items) == 1
    assert depth_2_items[0][1] == r1_1.message_id
