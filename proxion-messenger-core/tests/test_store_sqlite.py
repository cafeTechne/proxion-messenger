"""Tests for proxion_messenger_core.store_sqlite — SqliteStore.

All tests use an in-memory database (":memory:") so they are fast and leave
no files on disk.  A separate section tests file-backed persistence to verify
that messages survive a close/reopen cycle.
"""

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.sealed import mailbox_id_for, seal
from proxion_messenger_core.store import QuotaExceededError, StoreConfig
from proxion_messenger_core.store_sqlite import SqliteStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pub_bytes():
    return X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


@pytest.fixture
def mailbox(pub_bytes):
    return mailbox_id_for(pub_bytes)


@pytest.fixture
def envelope(pub_bytes):
    return seal(b"test message", pub_bytes)


@pytest.fixture
def store():
    with SqliteStore(":memory:") as s:
        yield s


# ---------------------------------------------------------------------------
# put / take_all
# ---------------------------------------------------------------------------

def test_put_returns_message_id(store, mailbox, envelope):
    msg_id = store.put(mailbox, envelope)
    assert isinstance(msg_id, str) and msg_id


def test_take_all_returns_message(store, mailbox, envelope):
    store.put(mailbox, envelope)
    msgs = store.take_all(mailbox)
    assert len(msgs) == 1
    assert msgs[0].envelope == envelope


def test_take_all_clears_mailbox(store, mailbox, envelope):
    store.put(mailbox, envelope)
    store.take_all(mailbox)
    assert store.peek(mailbox)["count"] == 0


def test_take_all_empty_mailbox(store, mailbox):
    assert store.take_all(mailbox) == []


def test_take_all_multiple_messages(store, mailbox, pub_bytes):
    for i in range(5):
        store.put(mailbox, seal(f"msg-{i}".encode(), pub_bytes))
    msgs = store.take_all(mailbox)
    assert len(msgs) == 5


def test_messages_ordered_oldest_first(store, mailbox, pub_bytes):
    ids = [store.put(mailbox, seal(b"x", pub_bytes)) for _ in range(3)]
    msgs = store.take_all(mailbox)
    assert [m.message_id for m in msgs] == ids


# ---------------------------------------------------------------------------
# list_all / take_by_ids
# ---------------------------------------------------------------------------

def test_list_all_does_not_drain(store, mailbox, envelope):
    store.put(mailbox, envelope)
    store.list_all(mailbox)
    assert store.peek(mailbox)["count"] == 1


def test_list_all_returns_messages(store, mailbox, envelope):
    store.put(mailbox, envelope)
    msgs = store.list_all(mailbox)
    assert len(msgs) == 1


def test_take_by_ids_removes_only_specified(store, mailbox, pub_bytes):
    id1 = store.put(mailbox, seal(b"a", pub_bytes))
    id2 = store.put(mailbox, seal(b"b", pub_bytes))
    id3 = store.put(mailbox, seal(b"c", pub_bytes))
    store.take_by_ids(mailbox, {id1, id3})
    remaining = store.list_all(mailbox)
    assert len(remaining) == 1
    assert remaining[0].message_id == id2


def test_take_by_ids_empty_set_leaves_all(store, mailbox, envelope):
    store.put(mailbox, envelope)
    store.take_by_ids(mailbox, set())
    assert store.peek(mailbox)["count"] == 1


def test_take_by_ids_all_ids_clears_mailbox(store, mailbox, pub_bytes):
    ids = {store.put(mailbox, seal(b"x", pub_bytes)) for _ in range(3)}
    store.take_by_ids(mailbox, ids)
    assert store.peek(mailbox)["count"] == 0


# ---------------------------------------------------------------------------
# peek
# ---------------------------------------------------------------------------

def test_peek_empty_mailbox(store, mailbox):
    info = store.peek(mailbox)
    assert info["count"] == 0
    assert info["bytes"] == 0
    assert info["oldest_age_s"] is None


def test_peek_reports_count_and_bytes(store, mailbox, envelope):
    store.put(mailbox, envelope)
    info = store.peek(mailbox)
    assert info["count"] == 1
    assert info["bytes"] > 0
    assert info["oldest_age_s"] is not None


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------

def test_max_messages_quota(pub_bytes):
    cfg = StoreConfig(max_messages=3, max_bytes=10 * 1024 * 1024, message_ttl=None)
    with SqliteStore(":memory:", cfg) as s:
        mailbox = mailbox_id_for(pub_bytes)
        for _ in range(3):
            s.put(mailbox, seal(b"x", pub_bytes))
        with pytest.raises(QuotaExceededError, match="3-message"):
            s.put(mailbox, seal(b"overflow", pub_bytes))


def test_max_bytes_quota(pub_bytes):
    cfg = StoreConfig(max_messages=1000, max_bytes=10, message_ttl=None)
    with SqliteStore(":memory:", cfg) as s:
        mailbox = mailbox_id_for(pub_bytes)
        with pytest.raises(QuotaExceededError, match="byte quota"):
            s.put(mailbox, seal(b"this is definitely more than 10 bytes", pub_bytes))


# ---------------------------------------------------------------------------
# TTL / expiry
# ---------------------------------------------------------------------------

def test_expired_messages_not_returned(pub_bytes):
    cfg = StoreConfig(max_messages=10, max_bytes=10 * 1024, message_ttl=0.01)
    with SqliteStore(":memory:", cfg) as s:
        mailbox = mailbox_id_for(pub_bytes)
        s.put(mailbox, seal(b"old", pub_bytes))
        time.sleep(0.05)
        msgs = s.take_all(mailbox)
        assert msgs == []


def test_expire_removes_across_mailboxes(pub_bytes):
    cfg = StoreConfig(max_messages=10, max_bytes=10 * 1024, message_ttl=0.01)
    with SqliteStore(":memory:", cfg) as s:
        k2 = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        mb1 = mailbox_id_for(pub_bytes)
        mb2 = mailbox_id_for(k2)
        s.put(mb1, seal(b"a", pub_bytes))
        s.put(mb2, seal(b"b", k2))
        time.sleep(0.05)
        removed = s.expire()
        assert removed == 2


def test_expire_no_ttl_removes_nothing(pub_bytes):
    cfg = StoreConfig(message_ttl=None)
    with SqliteStore(":memory:", cfg) as s:
        mailbox = mailbox_id_for(pub_bytes)
        s.put(mailbox, seal(b"x", pub_bytes))
        assert s.expire() == 0


# ---------------------------------------------------------------------------
# mailbox_count
# ---------------------------------------------------------------------------

def test_mailbox_count(pub_bytes):
    with SqliteStore(":memory:") as s:
        k2 = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert s.mailbox_count() == 0
        s.put(mailbox_id_for(pub_bytes), seal(b"a", pub_bytes))
        s.put(mailbox_id_for(k2), seal(b"b", k2))
        assert s.mailbox_count() == 2
        s.take_all(mailbox_id_for(pub_bytes))
        assert s.mailbox_count() == 1


# ---------------------------------------------------------------------------
# File-backed persistence — messages survive close/reopen
# ---------------------------------------------------------------------------

def test_messages_survive_reopen(tmp_path, pub_bytes):
    db = str(tmp_path / "store.db")
    mailbox = mailbox_id_for(pub_bytes)
    env = seal(b"persistent", pub_bytes)

    with SqliteStore(db) as s:
        msg_id = s.put(mailbox, env)

    # Reopen — message must still be there
    with SqliteStore(db) as s:
        msgs = s.list_all(mailbox)
        assert len(msgs) == 1
        assert msgs[0].message_id == msg_id
        assert msgs[0].envelope == env


def test_take_all_persists_drain(tmp_path, pub_bytes):
    db = str(tmp_path / "store.db")
    mailbox = mailbox_id_for(pub_bytes)

    with SqliteStore(db) as s:
        s.put(mailbox, seal(b"x", pub_bytes))
        s.take_all(mailbox)

    with SqliteStore(db) as s:
        assert s.list_all(mailbox) == []


# ---------------------------------------------------------------------------
# Handshake over SqliteStore
# ---------------------------------------------------------------------------

def test_handshake_over_sqlite_store():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from proxion_messenger_core import run_local_handshake
    from proxion_messenger_core.federation import Capability

    alice_id = Ed25519PrivateKey.generate()
    alice_store = X25519PrivateKey.generate()
    bob_id = Ed25519PrivateKey.generate()
    bob_store = X25519PrivateKey.generate()
    caps = [Capability(with_="stash://alice/shared/", can="read")]

    with SqliteStore(":memory:") as s:
        cert, valid = run_local_handshake(
            alice_id, alice_store, bob_id, bob_store, caps, caps, s
        )
    assert valid
    assert cert.issuer is not None
