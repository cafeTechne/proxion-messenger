"""Tests for offline message queue (Outbox)."""
from __future__ import annotations

import os
import shutil
import tempfile
import pytest
from proxion_messenger_core.outbox import Outbox
from proxion_messenger_core.messaging import Message, compose
from proxion_messenger_core import AgentState, run_local_handshake
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.store import MemoryStore

@pytest.fixture
def outbox_dir():
    dirpath = tempfile.mkdtemp()
    yield dirpath
    shutil.rmtree(dirpath)

@pytest.fixture
def outbox(outbox_dir):
    return Outbox(outbox_dir)

@pytest.fixture
def alice():
    return AgentState.generate()

@pytest.fixture
def cert(alice):
    bob = AgentState.generate()
    store = MemoryStore()
    caps = [Capability(can="read", with_="stash://messages/")]
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

def test_outbox_enqueue_persistence(alice, cert, outbox, outbox_dir):
    msg = compose(alice.identity_key, cert, "hello")
    outbox.enqueue(msg, target_cert_id="cert123")
    
    # Check file exists
    path = os.path.join(outbox_dir, f"{msg.message_id}.json")
    assert os.path.exists(path)
    
    # Check items retrieval
    items = outbox.get_items()
    assert len(items) == 1
    assert items[0].item_id == msg.message_id
    assert items[0].message.content == "hello"
    assert items[0].target_cert_id == "cert123"

def test_outbox_remove(alice, cert, outbox):
    msg = compose(alice.identity_key, cert, "bye")
    outbox.enqueue(msg)
    assert len(outbox.get_items()) == 1
    
    outbox.remove(msg.message_id)
    assert len(outbox.get_items()) == 0

def test_outbox_clear(alice, cert, outbox):
    outbox.enqueue(compose(alice.identity_key, cert, "1"))
    outbox.enqueue(compose(alice.identity_key, cert, "2"))
    assert len(outbox.get_items()) == 2
    
    outbox.clear()
    assert len(outbox.get_items()) == 0

def test_outbox_remove_missing(outbox):
    # Removing non-existent item should not raise exception
    outbox.remove("non-existent-id")
    assert len(outbox.get_items()) == 0
