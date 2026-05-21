"""Tests for STUN session persistence in LocalStore."""
import time

import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_save_and_get_latest_stun_session(store):
    store.save_stun_session("s1", "203.0.113.5", 4321, "stun.l.google.com")
    session = store.get_latest_stun_session()
    assert session is not None
    assert session["external_ip"] == "203.0.113.5"
    assert session["external_port"] == 4321
    assert session["stun_server"] == "stun.l.google.com"


def test_get_latest_stun_session_returns_newest(store):
    store.save_stun_session("s1", "1.2.3.4", 1000, "stun.a.com", ttl_seconds=300)
    time.sleep(0.01)
    store.save_stun_session("s2", "5.6.7.8", 2000, "stun.b.com", ttl_seconds=300)
    session = store.get_latest_stun_session()
    assert session["external_ip"] == "5.6.7.8"


def test_get_latest_stun_session_ignores_expired(store):
    store.save_stun_session("expired-s", "9.9.9.9", 9999, "stun.example.com", ttl_seconds=0)
    time.sleep(0.01)
    session = store.get_latest_stun_session()
    assert session is None


def test_prune_expired_stun_sessions(store):
    store.save_stun_session("fresh", "1.1.1.1", 100, "stun.a.com", ttl_seconds=300)
    store.save_stun_session("stale", "2.2.2.2", 200, "stun.b.com", ttl_seconds=0)
    time.sleep(0.01)
    pruned = store.prune_expired_stun_sessions()
    assert pruned >= 1
    remaining = store.get_latest_stun_session()
    assert remaining is not None
    assert remaining["external_ip"] == "1.1.1.1"


def test_stun_session_ttl_default(store):
    store.save_stun_session("s-default", "10.0.0.1", 51820, "stun.example.com")
    session = store.get_latest_stun_session()
    assert session is not None
    assert session["expires_at"] > time.time() + 200  # well within 300s TTL
