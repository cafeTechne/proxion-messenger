"""Relay authz hardening: DM-edit author check + privileged-relay binding.

Covers two gateway auth fixes:
  - the dm_edit relay must not rewrite a message the peer did not author
    (update_message is a global UPDATE by message_id, like the delete relay);
  - moderation/emoji relays must not be authorized by a first-use TOFU seed of an
    empty relaygw slot (owner-impersonation over /relay).
"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.didkey import pub_key_to_did


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9992, db_path=str(tmp_path / "t.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "t.db"))
    return gw


def _did():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pub_key_to_did(pub)


@pytest.mark.asyncio
async def test_dm_edit_relay_rejects_non_author(gateway):
    peer = _did()
    author = _did()
    gateway._store.save_relationship({"certificate_id": "cert-x", "subject": "aa" * 32}, peer_did=peer)
    gateway._store.save_message("m-auth", "cert-x", "local_dm", author, "Author", "original", "2026-01-01T00:00:00Z")
    status, _ = await gateway._handle_dm_edit_relay({
        "from_webid": peer, "to_webid": author, "message_id": "m-auth", "new_content": "HACKED",
    })
    assert status.startswith("200")
    assert gateway._store.get_message("m-auth")["content"] == "original"


@pytest.mark.asyncio
async def test_dm_edit_relay_allows_author(gateway):
    peer = _did()
    gateway._store.save_relationship({"certificate_id": "cert-y", "subject": "bb" * 32}, peer_did=peer)
    gateway._store.save_message("m-own", "cert-y", "local_dm", peer, "Peer", "original", "2026-01-01T00:00:00Z")
    status, _ = await gateway._handle_dm_edit_relay({
        "from_webid": peer, "to_webid": peer, "message_id": "m-own", "new_content": "edited",
    })
    assert status.startswith("200")
    assert gateway._store.get_message("m-own")["content"] == "edited"


def test_privileged_relay_requires_established_binding(gateway):
    owner = _did()
    sig_did = _did()
    # Empty slot + require_established → refused, and NOT seeded.
    assert gateway._relay_sender_gateway_ok(owner, sig_did, require_established=True) is False
    assert not gateway._store.get_identity_key_history("relaygw:" + owner)
    # An ordinary relay seeds the binding (TOFU) ...
    assert gateway._relay_sender_gateway_ok(owner, sig_did) is True
    # ... after which the privileged path accepts that SAME signer ...
    assert gateway._relay_sender_gateway_ok(owner, sig_did, require_established=True) is True
    # ... but still refuses a DIFFERENT signer.
    assert gateway._relay_sender_gateway_ok(owner, _did(), require_established=True) is False
