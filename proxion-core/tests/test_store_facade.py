"""Tests: LocalStore facade after the R36 domain decomposition.

Guards against a domain mixin being dropped from the facade (which would
silently remove a chunk of the store's API) and confirms a method from each
domain resolves and round-trips.
"""
from __future__ import annotations
import os
import tempfile
from datetime import datetime, timezone
import pytest
from proxion_messenger_core.local_store import LocalStore


def _store():
    return LocalStore(os.path.join(tempfile.mkdtemp(), "facade.db"))


def test_one_method_per_domain_is_present():
    """A representative method from each domain mixin must exist on LocalStore."""
    s = _store()
    for name in [
        "save_message",          # messages
        "add_room_member",       # rooms
        "get_dm_threads",        # dms
        "save_peer_gateway",     # federation
        "register_device",       # devices
        "get_display_name",      # identity
        "save_security_event",   # security
        "enqueue_mailbox",       # federation (R38)
        "ban_room_member",       # rooms (R32)
    ]:
        assert hasattr(s, name), f"missing store method: {name}"
        assert callable(getattr(s, name))


def test_message_roundtrip_through_facade():
    s = _store()
    ts = datetime.now(timezone.utc).isoformat()
    s.save_message("m1", "r1", "local_room", "did:key:zAlice", "Alice", "hello", ts)
    msgs = s.get_messages("r1")
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello"


def test_schema_version_intact():
    import sqlite3
    s = _store()
    v = sqlite3.connect(s.db_path).execute("SELECT version FROM schema_version").fetchone()[0]
    assert v == 54
