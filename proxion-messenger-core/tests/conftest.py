"""Shared pytest fixtures for proxion-messenger-core tests.

All fixtures are function-scoped by default (fresh state per test) unless
explicitly marked otherwise.  This prevents state leakage between tests.

Note on test ordering
---------------------
E2E tests (tests/e2e/) spin up a session-scoped gateway server in a background
thread.  That thread keeps its own asyncio event loop alive for the entire
session, which can corrupt the async-fixture cleanup of unit tests that run
after it.  To avoid this, E2E items are sorted to the very end of the
collection order via the pytest_collection_modifyitems hook below.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import (
    MemoryStore,
    RevocationList,
    issue_token,
    fingerprint_from_key,
    run_local_handshake,
)
from proxion_messenger_core.federation import Capability


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(items):
    """Move E2E tests to the end so their background thread doesn't disturb unit tests."""
    e2e_dir = Path(__file__).parent / "e2e"
    e2e, rest = [], []
    for item in items:
        if Path(item.fspath).parent == e2e_dir:
            e2e.append(item)
        else:
            rest.append(item)
    items[:] = rest + e2e


@pytest.fixture(autouse=True)
def _disable_require_auth_by_default(monkeypatch):
    """Unit tests run with auth disabled so register commands work without a challenge.

    Tests that specifically exercise the auth challenge flow override this with
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1").
    E2E tests tolerate both states (helpers.py handles auth_challenge if present).
    """
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    # Allow short passphrases in all tests except those in test_passphrase_policy.py
    # which explicitly test passphrase strength enforcement.
    monkeypatch.setenv("PROXION_ALLOW_WEAK_PASSPHRASE", "1")


@pytest.fixture(autouse=True)
def _shutdown_test_gateways():
    """Stop any real gateway a test started in a background thread.

    Without this, every gateway started by tests/gwharness.py would outlive its
    test, holding a thread, an event loop and two listening sockets for the rest
    of the session. That accumulation (43 call sites across the suite) is what
    made timing-sensitive socket tests flake on slow machines.
    """
    yield
    import gwharness
    gwharness.shutdown_all()


@pytest.fixture
def now() -> datetime:
    """Current UTC time, timezone-aware."""
    return datetime.now(timezone.utc)


@pytest.fixture
def exp(now: datetime) -> datetime:
    """Token expiry: now + 1 hour."""
    return now + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Signing key (for token HMAC)
# ---------------------------------------------------------------------------

@pytest.fixture
def signing_key() -> bytes:
    """Fresh 32-byte random HMAC signing key."""
    return os.urandom(32)


# ---------------------------------------------------------------------------
# Alice keypairs
# ---------------------------------------------------------------------------

@pytest.fixture
def alice_identity_priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def alice_identity_pub(alice_identity_priv: Ed25519PrivateKey):
    return alice_identity_priv.public_key()


@pytest.fixture
def alice_store_priv() -> X25519PrivateKey:
    return X25519PrivateKey.generate()


@pytest.fixture
def alice_store_pub_bytes(alice_store_priv: X25519PrivateKey) -> bytes:
    return alice_store_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


# ---------------------------------------------------------------------------
# Bob keypairs
# ---------------------------------------------------------------------------

@pytest.fixture
def bob_identity_priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def bob_identity_pub(bob_identity_priv: Ed25519PrivateKey):
    return bob_identity_priv.public_key()


@pytest.fixture
def bob_store_priv() -> X25519PrivateKey:
    return X25519PrivateKey.generate()


@pytest.fixture
def bob_store_pub_bytes(bob_store_priv: X25519PrivateKey) -> bytes:
    return bob_store_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

@pytest.fixture
def store() -> MemoryStore:
    """Fresh in-memory Coordination Store."""
    return MemoryStore()


@pytest.fixture
def alice_rl() -> RevocationList:
    return RevocationList()


@pytest.fixture
def bob_rl() -> RevocationList:
    return RevocationList()


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

@pytest.fixture
def bob_holder_fp(bob_identity_pub) -> str:
    """Bob's holder key fingerprint (for embedding in tokens Alice issues him)."""
    return fingerprint_from_key(bob_identity_pub)


@pytest.fixture
def basic_token(signing_key, bob_holder_fp, now, exp):
    """A minimal valid capability token issued to Bob."""
    return issue_token(
        permissions=[("read", "/data/")],
        exp=exp,
        aud="alice.proxion.local",
        caveats=[],
        holder_key_fingerprint=bob_holder_fp,
        signing_key=signing_key,
        now=now,
    )


# ---------------------------------------------------------------------------
# Federation
# ---------------------------------------------------------------------------

@pytest.fixture
def shared_caps() -> list:
    """Default capability set used in handshake fixtures."""
    return [Capability(with_="stash://alice/shared/bob/", can="read")]


@pytest.fixture
def cert(
    alice_identity_priv, alice_store_priv,
    bob_identity_priv, bob_store_priv,
    shared_caps, store,
):
    """A fully completed RelationshipCertificate between Alice and Bob."""
    certificate, cert_valid = run_local_handshake(
        alice_identity_priv, alice_store_priv,
        bob_identity_priv, bob_store_priv,
        shared_caps, shared_caps,
        store,
    )
    assert cert_valid, "fixture: handshake cert verification failed"
    return certificate
