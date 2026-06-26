"""Tests for proxion_messenger_core.store — MemoryStore coordination store."""

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.sealed import mailbox_id_for, seal
from proxion_messenger_core.store import MemoryStore, QuotaExceededError, StoreConfig


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
    return MemoryStore()


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
    assert info["bytes"] == envelope.byte_size
    assert info["oldest_age_s"] is not None


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------

def test_max_messages_quota(pub_bytes):
    cfg = StoreConfig(max_messages=3, max_bytes=10 * 1024 * 1024, message_ttl=None)
    s = MemoryStore(cfg)
    mailbox = mailbox_id_for(pub_bytes)
    for _ in range(3):
        s.put(mailbox, seal(b"x", pub_bytes))
    with pytest.raises(QuotaExceededError, match="3-message"):
        s.put(mailbox, seal(b"overflow", pub_bytes))


def test_max_bytes_quota(pub_bytes):
    cfg = StoreConfig(max_messages=1000, max_bytes=10, message_ttl=None)
    s = MemoryStore(cfg)
    mailbox = mailbox_id_for(pub_bytes)
    with pytest.raises(QuotaExceededError, match="byte quota"):
        s.put(mailbox, seal(b"this is definitely more than 10 bytes", pub_bytes))


# ---------------------------------------------------------------------------
# TTL / expiry
# ---------------------------------------------------------------------------

def test_expired_messages_not_returned(pub_bytes):
    cfg = StoreConfig(max_messages=10, max_bytes=10 * 1024, message_ttl=0.01)
    s = MemoryStore(cfg)
    mailbox = mailbox_id_for(pub_bytes)
    s.put(mailbox, seal(b"old", pub_bytes))
    time.sleep(0.05)
    msgs = s.take_all(mailbox)
    assert msgs == []


def test_expire_removes_across_mailboxes(pub_bytes):
    cfg = StoreConfig(max_messages=10, max_bytes=10 * 1024, message_ttl=0.01)
    s = MemoryStore(cfg)
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
    s = MemoryStore(cfg)
    mailbox = mailbox_id_for(pub_bytes)
    s.put(mailbox, seal(b"x", pub_bytes))
    assert s.expire() == 0


# ---------------------------------------------------------------------------
# mailbox_count
# ---------------------------------------------------------------------------

def test_mailbox_count(pub_bytes):
    s = MemoryStore()
    k2 = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert s.mailbox_count() == 0
    s.put(mailbox_id_for(pub_bytes), seal(b"a", pub_bytes))
    s.put(mailbox_id_for(k2), seal(b"b", k2))
    assert s.mailbox_count() == 2
    s.take_all(mailbox_id_for(pub_bytes))
    assert s.mailbox_count() == 1


def test_max_mailboxes_quota():
    cfg = StoreConfig(max_mailboxes=3, message_ttl=None)
    s = MemoryStore(cfg)
    keys = [
        X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        for _ in range(4)
    ]
    for k in keys[:3]:
        s.put(mailbox_id_for(k), seal(b"hi", k))
    # 4th distinct mailbox must be rejected
    with pytest.raises(QuotaExceededError, match="3-mailbox"):
        s.put(mailbox_id_for(keys[3]), seal(b"hi", keys[3]))


def test_max_mailboxes_allows_existing_mailbox():
    """Adding a second message to an existing mailbox must not count as a new mailbox."""
    cfg = StoreConfig(max_mailboxes=1, message_ttl=None)
    s = MemoryStore(cfg)
    k = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    mailbox = mailbox_id_for(k)
    s.put(mailbox, seal(b"first", k))
    # Same mailbox — no QuotaExceededError expected
    s.put(mailbox, seal(b"second", k))
