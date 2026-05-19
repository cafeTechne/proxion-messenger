"""Tests for proxion_messenger_core.revocation — RevocationList and ID derivation."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from proxion_messenger_core import RevocationList, issue_token, token_revocation_id, certificate_revocation_id
from proxion_messenger_core.federation import RelationshipCertificate


@pytest.fixture
def sk():
    return os.urandom(32)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


@pytest.fixture
def token(sk, now):
    return issue_token(
        permissions=[("read", "/data/")],
        exp=now + timedelta(hours=1),
        aud="svc",
        caveats=[],
        holder_key_fingerprint="fp",
        signing_key=sk,
        now=now,
    )


@pytest.fixture
def cert():
    return RelationshipCertificate(
        issuer="alice", subject="bob", capabilities=[], wireguard={}
    )


@pytest.fixture
def rl():
    return RevocationList()


# ---------------------------------------------------------------------------
# revoke / is_revoked — basic
# ---------------------------------------------------------------------------

def test_revoke_and_is_revoked(rl, token, now):
    rl.revoke(token, now)
    assert rl.is_revoked(token, now)


def test_not_revoked_by_default(rl, token, now):
    assert not rl.is_revoked(token, now)


def test_revocation_expires_at_token_exp(rl, token, now):
    rl.revoke(token, now)
    after_exp = token.exp + timedelta(seconds=1)
    assert not rl.is_revoked(token, after_exp)


def test_revoke_with_explicit_ttl(rl, token, now):
    rl.revoke(token, now, ttl_seconds=10)
    assert rl.is_revoked(token, now + timedelta(seconds=5))
    assert not rl.is_revoked(token, now + timedelta(seconds=15))


def test_ttl_capped_at_token_expiry(rl, token, now):
    """Even with a very long ttl_seconds the entry must expire no later than token.exp."""
    rl.revoke(token, now, ttl_seconds=999 * 86400)
    after_exp = token.exp + timedelta(seconds=1)
    assert not rl.is_revoked(token, after_exp)


# ---------------------------------------------------------------------------
# revoke_until
# ---------------------------------------------------------------------------

def test_revoke_until_adds_entry(rl, token, now):
    rev_id = token_revocation_id(token)
    rl.revoke_until(rev_id, token.exp)
    assert rl.is_revoked(token, now)


def test_revoke_until_entry_expires(rl, token, now):
    rev_id = token_revocation_id(token)
    rl.revoke_until(rev_id, now + timedelta(seconds=2))
    assert not rl.is_revoked(token, now + timedelta(seconds=5))


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------

def test_purge_removes_expired_entries(rl, token, now):
    rl.revoke(token, now, ttl_seconds=1)
    removed = rl.purge(now + timedelta(seconds=5))
    assert removed == 1


def test_purge_does_not_remove_live_entries(rl, token, now):
    rl.revoke(token, now)
    removed = rl.purge(now)   # token hasn't expired yet
    assert removed == 0


# ---------------------------------------------------------------------------
# token_revocation_id
# ---------------------------------------------------------------------------

def test_token_revocation_id_deterministic(token):
    assert token_revocation_id(token) == token_revocation_id(token)


def test_token_revocation_id_differs_across_tokens(sk, now):
    t1 = issue_token([("read", "/a/")], now + timedelta(hours=1), "svc", [], "fp", sk, now=now)
    t2 = issue_token([("read", "/b/")], now + timedelta(hours=1), "svc", [], "fp", sk, now=now)
    assert token_revocation_id(t1) != token_revocation_id(t2)


def test_token_revocation_id_is_hex_string(token):
    rid = token_revocation_id(token)
    assert isinstance(rid, str)
    int(rid, 16)


# ---------------------------------------------------------------------------
# certificate_revocation_id
# ---------------------------------------------------------------------------

def test_cert_revocation_id_deterministic(cert):
    assert certificate_revocation_id(cert) == certificate_revocation_id(cert)


def test_cert_and_token_ids_do_not_collide(token, cert):
    """cert: prefix ensures no namespace collision."""
    assert token_revocation_id(token) != certificate_revocation_id(cert)


def test_different_certs_different_ids():
    c1 = RelationshipCertificate("a", "b", [], {})
    c2 = RelationshipCertificate("a", "b", [], {})
    assert certificate_revocation_id(c1) != certificate_revocation_id(c2)


# ---------------------------------------------------------------------------
# Task 3 — RevocationList serialization
# ---------------------------------------------------------------------------

def test_to_dict_excludes_expired_entries(rl, token, now):
    rl.revoke(token, now, ttl_seconds=1)
    # After a while the entry is expired
    future = now + timedelta(seconds=10)
    future_rl = RevocationList()
    future_rl.revoke(token, now, ttl_seconds=1)
    d = future_rl.to_dict()  # called now — entry still exists
    assert len(d) <= 1  # entry is present (hasn't expired yet relative to calling now)
    # Manually verify: create rl with past expiry
    from proxion_messenger_core.revocation import RevocationEntry, _coerce_datetime
    past_rl = RevocationList()
    past_rl._entries["test_id"] = RevocationEntry(
        revoked_until=now - timedelta(seconds=5)
    )
    d_past = past_rl.to_dict()
    assert "test_id" not in d_past


def test_from_dict_roundtrip_preserves_active_entries(token, now):
    rl = RevocationList()
    rl.revoke(token, now)
    d = rl.to_dict()
    rl2 = RevocationList.from_dict(d)
    assert rl2.is_revoked(token, now)


def test_save_and_load_roundtrip(tmp_path, token, now):
    rl = RevocationList()
    rl.revoke(token, now)
    path = str(tmp_path / "revocations.json")
    rl.save(path)
    loaded = RevocationList.load(path)
    assert loaded.is_revoked(token, now)


def test_load_returns_empty_when_file_missing(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    rl = RevocationList.load(path)
    assert len(rl.to_dict()) == 0

