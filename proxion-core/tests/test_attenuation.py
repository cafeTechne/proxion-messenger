"""Tests for proxion_messenger_core.attenuation — monotonic token derivation."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from proxion_messenger_core.attenuation import derive_token
from proxion_messenger_core.context import Caveat
from proxion_messenger_core.errors import AttenuationError
from proxion_messenger_core.tokens import issue_token, verify_integrity


def ip_allowlist(allowed: set) -> Caveat:
    return Caveat(id=f"ip:{','.join(sorted(allowed))}", predicate=lambda ctx: ctx.ip in allowed)


def nonce_matches(expected: str) -> Caveat:
    return Caveat(id=f"nonce:{expected}", predicate=lambda ctx: ctx.device_nonce == expected)


@pytest.fixture
def sk():
    return os.urandom(32)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


@pytest.fixture
def parent(sk, now):
    return issue_token(
        permissions=[("read", "/data/"), ("write", "/data/")],
        exp=now + timedelta(hours=2),
        aud="svc",
        caveats=[],
        holder_key_fingerprint="fp-parent",
        signing_key=sk,
        now=now,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_derive_narrower_perms(parent, sk, now):
    child = derive_token(
        parent=parent,
        narrower_perms=[("read", "/data/")],   # subset of parent
        extra_caveats=[],
        now=now,
        signing_key=sk,
    )
    assert child.permissions == frozenset([("read", "/data/")])


def test_derived_token_valid_signature(parent, sk, now):
    child = derive_token(parent, [("read", "/data/")], [], now, sk)
    assert verify_integrity(child, sk)


def test_derived_token_inherits_holder_fingerprint(parent, sk, now):
    child = derive_token(parent, [("read", "/data/")], [], now, sk)
    assert child.holder_key_fingerprint == parent.holder_key_fingerprint


def test_derived_token_inherits_expiry(parent, sk, now):
    child = derive_token(parent, [("read", "/data/")], [], now, sk)
    assert child.exp == parent.exp


def test_derived_token_inherits_parent_caveats(sk, now):
    caveat = ip_allowlist({"127.0.0.1"})
    parent = issue_token(
        permissions=[("read", "/"), ("write", "/")],
        exp=now + timedelta(hours=1),
        aud="svc",
        caveats=[caveat],
        holder_key_fingerprint="fp",
        signing_key=sk,
        now=now,
    )
    child = derive_token(parent, [("read", "/")], [], now, sk)
    assert len(child.caveats) == 1
    assert child.caveats[0].id == caveat.id


def test_extra_caveats_appended(parent, sk, now):
    extra = nonce_matches("nonce-42")
    child = derive_token(parent, [("read", "/data/")], [extra], now, sk)
    caveat_ids = {c.id for c in child.caveats}
    assert extra.id in caveat_ids


def test_same_perms_allowed(parent, sk, now):
    """Deriving with the exact same permission set is not widening."""
    child = derive_token(parent, list(parent.permissions), [], now, sk)
    assert child.permissions == parent.permissions


# ---------------------------------------------------------------------------
# Monotonicity violations
# ---------------------------------------------------------------------------

def test_widening_perms_raises(parent, sk, now):
    with pytest.raises(AttenuationError, match="widening"):
        derive_token(
            parent,
            [("read", "/data/"), ("delete", "/data/")],   # "delete" not in parent
            [],
            now,
            sk,
        )


def test_empty_perms_raises(parent, sk, now):
    with pytest.raises(AttenuationError):
        derive_token(parent, [], [], now, sk)


def test_expired_parent_raises(parent, sk):
    past = parent.exp + timedelta(seconds=1)
    with pytest.raises(AttenuationError, match="expired"):
        derive_token(parent, [("read", "/data/")], [], past, sk)
