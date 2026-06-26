"""Tests for proxion_messenger_core.tokens — Token issuance and integrity."""

import os
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from proxion_messenger_core.errors import TokenError
from proxion_messenger_core.tokens import issue_token, verify_integrity, token_canonical_bytes


@pytest.fixture
def sk():
    return os.urandom(32)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


@pytest.fixture
def exp(now):
    return now + timedelta(hours=1)


@pytest.fixture
def token(sk, now, exp):
    return issue_token(
        permissions=[("read", "/data/")],
        exp=exp,
        aud="aud1",
        caveats=[],
        holder_key_fingerprint="fp-abc",
        signing_key=sk,
        now=now,
    )


# ---------------------------------------------------------------------------
# Issuance — happy path
# ---------------------------------------------------------------------------

def test_issue_returns_token(token):
    assert token is not None


def test_token_has_expected_fields(token):
    assert token.aud == "aud1"
    assert token.holder_key_fingerprint == "fp-abc"
    assert ("read", "/data/") in token.permissions
    assert token.alg == "HMAC-SHA256"
    assert token.signature


def test_token_id_is_unique(sk, now, exp):
    t1 = issue_token([("read", "/")], exp, "a", [], "fp", sk, now=now)
    t2 = issue_token([("read", "/")], exp, "a", [], "fp", sk, now=now)
    assert t1.token_id != t2.token_id


def test_explicit_token_id(sk, now, exp):
    t = issue_token([("read", "/")], exp, "a", [], "fp", sk, now=now, token_id="my-id")
    assert t.token_id == "my-id"


def test_permissions_stored_as_frozenset(token):
    assert isinstance(token.permissions, frozenset)


def test_multiple_permissions(sk, now, exp):
    perms = [("read", "/data/"), ("write", "/data/"), ("delete", "/tmp/")]
    t = issue_token(perms, exp, "a", [], "fp", sk, now=now)
    assert frozenset(perms) == t.permissions


# ---------------------------------------------------------------------------
# Issuance — validation errors
# ---------------------------------------------------------------------------

def test_empty_permissions_raises(sk, now, exp):
    with pytest.raises(TokenError):
        issue_token([], exp, "a", [], "fp", sk, now=now)


def test_expired_raises(sk, now):
    with pytest.raises(TokenError, match="expiration"):
        issue_token([("read", "/")], now - timedelta(seconds=1), "a", [], "fp", sk, now=now)


def test_exp_equal_to_now_raises(sk, now):
    with pytest.raises(TokenError):
        issue_token([("read", "/")], now, "a", [], "fp", sk, now=now)


# ---------------------------------------------------------------------------
# Timezone coercion
# ---------------------------------------------------------------------------

def test_naive_exp_accepted(sk, now):
    naive_exp = (now + timedelta(hours=1)).replace(tzinfo=None)
    t = issue_token([("read", "/")], naive_exp, "a", [], "fp", sk, now=now)
    assert t.exp.tzinfo is not None


# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------

def test_verify_integrity_passes(token, sk):
    assert verify_integrity(token, sk) is True


def test_verify_integrity_wrong_key_raises(token):
    with pytest.raises(TokenError, match="signature mismatch"):
        verify_integrity(token, os.urandom(32))


def test_verify_integrity_tampered_signature_raises(token, sk):
    bad = replace(token, signature="AAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    with pytest.raises(TokenError, match="signature mismatch"):
        verify_integrity(bad, sk)


def test_verify_integrity_wrong_alg_raises(token, sk):
    bad = replace(token, alg="RS256")
    with pytest.raises(TokenError, match="unsupported alg"):
        verify_integrity(bad, sk)


# ---------------------------------------------------------------------------
# Canonical bytes
# ---------------------------------------------------------------------------

def test_canonical_bytes_deterministic(token):
    assert token_canonical_bytes(token) == token_canonical_bytes(token)


def test_canonical_bytes_changes_with_permission(sk, now, exp):
    t1 = issue_token([("read", "/a/")], exp, "a", [], "fp", sk, now=now)
    t2 = issue_token([("read", "/b/")], exp, "a", [], "fp", sk, now=now)
    assert token_canonical_bytes(t1) != token_canonical_bytes(t2)
