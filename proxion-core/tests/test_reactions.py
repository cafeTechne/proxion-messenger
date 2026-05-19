"""Tests for proxion_messenger_core.reactions."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from proxion_messenger_core import AgentState, run_local_handshake
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.messaging import compose, send, Message
from proxion_messenger_core.reactions import add_reaction, remove_reaction, get_reactions
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

def test_add_reaction_sends_reaction_message(alice, cert):
    pod = MagicMock()
    target_id = "msg123"
    react = add_reaction(cert, pod, alice.identity_key, target_id, "👍")
    
    assert react.emoji == "👍"
    assert react.message_id == target_id
    assert react.sender_webid == alice.identity_pub_bytes.hex()
    
    pod.put.assert_called_once()
    raw_data = pod.put.call_args[0][1]
    data = json.loads(raw_data.decode())
    assert data["message_type"] == "reaction"
    assert data["reply_to_id"] == target_id
    assert json.loads(data["content"])["emoji"] == "👍"

def test_remove_reaction_deletes_message(alice, cert):
    pod = MagicMock()
    remove_reaction(cert, pod, "react456")
    pod.delete.assert_called_once()
    path = pod.delete.call_args[0][0]
    assert "react456.json" in path

def test_get_reactions_aggregates_multiple_emojis(alice, bob, cert):
    m1 = compose(alice.identity_key, cert, "msg1")
    # Reaction 1: Alice reacts 👍 to m1
    r1 = compose(alice.identity_key, cert, json.dumps({"emoji": "👍", "target": m1.message_id}), 
                 reply_to_id=m1.message_id, message_type="reaction")
    # Reaction 2: Bob reacts 👍 to m1
    r2 = compose(bob.identity_key, cert, json.dumps({"emoji": "👍", "target": m1.message_id}), 
                 reply_to_id=m1.message_id, message_type="reaction")
    # Reaction 3: Alice reacts ❤️ to m1
    r3 = compose(alice.identity_key, cert, json.dumps({"emoji": "❤️", "target": m1.message_id}), 
                 reply_to_id=m1.message_id, message_type="reaction")
    
    msgs = [m1, r1, r2, r3]
    reacts = get_reactions(msgs, m1.message_id)
    
    assert reacts["👍"] == [alice.identity_pub_bytes.hex(), bob.identity_pub_bytes.hex()]
    assert reacts["❤️"] == [alice.identity_pub_bytes.hex()]

def test_get_reactions_ignores_other_messages(alice, cert):
    m1 = compose(alice.identity_key, cert, "msg1")
    m2 = compose(alice.identity_key, cert, "msg2")
    # Reaction to m2
    r_m2 = compose(alice.identity_key, cert, json.dumps({"emoji": "👍", "target": m2.message_id}), 
                   reply_to_id=m2.message_id, message_type="reaction")
    
    msgs = [m1, m2, r_m2]
    reacts = get_reactions(msgs, m1.message_id)
    assert reacts == {}

def test_get_reactions_ignores_malformed_content(alice, cert):
    m1 = compose(alice.identity_key, cert, "msg1")
    # Malformed reaction message
    bad_r = compose(alice.identity_key, cert, "not json", 
                    reply_to_id=m1.message_id, message_type="reaction")
    
    msgs = [m1, bad_r]
    reacts = get_reactions(msgs, m1.message_id)
    assert reacts == {}
