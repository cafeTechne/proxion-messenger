"""Task 16 — SqliteStore as token ledger integration tests."""

import os
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import MemoryStore, RevocationList, run_local_handshake
from proxion_messenger_core.certtoken import (
    issue_from_certificate,
    revoke_tokens_via_ledger,
)
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.store_sqlite import SqliteStore


@pytest.fixture
def sk():
    return os.urandom(32)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


@pytest.fixture
def cert():
    alice_id = Ed25519PrivateKey.generate()
    alice_store = X25519PrivateKey.generate()
    bob_id = Ed25519PrivateKey.generate()
    bob_store = X25519PrivateKey.generate()
    caps = [Capability(with_="stash://alice/shared/bob/", can="read")]
    store = MemoryStore()
    certificate, valid = run_local_handshake(
        alice_id, alice_store, bob_id, bob_store, caps, caps, store
    )
    assert valid
    return certificate, alice_id, bob_id


def test_issue_into_sqlite_ledger(cert, sk, now):
    """issue_from_certificate with a SqliteStore records a ledger entry."""
    certificate, alice_id, bob_id = cert
    ledger = SqliteStore(":memory:")

    token = issue_from_certificate(
        cert=certificate,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=bob_id.public_key(),
        signing_key=sk,
        now=now,
        store=ledger,
    )

    mailbox = f"token-ledger/{certificate.certificate_id}"
    entries = ledger.list_all(mailbox)
    assert len(entries) == 1

    import json
    data = json.loads(entries[0].envelope.ciphertext.decode("utf-8"))
    from proxion_messenger_core.revocation import token_revocation_id
    assert data["token_rev_id"] == token_revocation_id(token)


def test_revoke_via_sqlite_ledger(cert, sk, now):
    """revoke_tokens_via_ledger reads from SqliteStore and revokes all tokens."""
    certificate, alice_id, bob_id = cert
    ledger = SqliteStore(":memory:")

    tokens = [
        issue_from_certificate(
            cert=certificate,
            requested_permissions=[("read", "stash://alice/shared/bob/")],
            holder_pub_key=bob_id.public_key(),
            signing_key=sk,
            now=now,
            store=ledger,
        )
        for _ in range(3)
    ]

    rl = RevocationList()
    count = revoke_tokens_via_ledger(certificate, ledger, rl)
    assert count == 3
    for tok in tokens:
        assert rl.is_revoked(tok, now)
