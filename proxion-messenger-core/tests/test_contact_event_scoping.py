"""contact_revoked must reach only the revoker's own identity, not every client.

Was self.broadcast() — leaking (cert_id, peer_did) to all sessions on the
gateway (a relationship-metadata leak on a shared gateway). Now scoped to the
caller via _send_to_identity, which also correctly fans to all of the caller's
own devices so each purges the contact + cached DM plaintext. contact_added
(HTTP accept-invite path) uses the same scoping and is additionally covered
against a broadcast regression by test_broadcast_scoping_guard.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _got(ws, type_):
    return any(json.loads(c[0][0]).get("type") == type_ for c in ws.send.call_args_list)


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "revoke.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_contact_revoked_only_reaches_revoker(gateway, noauth_env, monkeypatch):
    revoker = Ed25519PrivateKey.generate()
    stranger = Ed25519PrivateKey.generate()
    revoker_did = _did(revoker)

    ws_revoker = _mock_ws()
    ws_stranger = _mock_ws()
    await _register(gateway, ws_revoker, revoker_did)
    await _register(gateway, ws_stranger, _did(stranger))

    cert_id = "cert-abc"
    peer_did = "did:key:zPeer"
    # Seed the relationship the revoke looks up; stub the write.
    monkeypatch.setattr(gateway._store, "get_relationship_by_cert_id",
                        lambda cid: {"peer_did": peer_did} if cid == cert_id else None)
    monkeypatch.setattr(gateway._store, "mark_revoked", lambda *a, **k: None)

    for ws in (ws_revoker, ws_stranger):
        ws.send.reset_mock()
    await gateway.process_command(ws_revoker, {"cmd": "revoke_contact", "cert_id": cert_id})

    assert _got(ws_revoker, "contact_revoked"), "the revoker should be told to purge the contact"
    assert not _got(ws_stranger, "contact_revoked"), "an unrelated client must NOT learn of the revoke"


@pytest.mark.asyncio
async def test_contact_revoked_falls_back_to_broadcast_when_uncredentialed(gateway, monkeypatch):
    """If the caller isn't identified (no webid), fall back to broadcast — the
    single-user-safe path (their own sessions only)."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    ws = _mock_ws()
    # Register then blank the caller's webid to simulate the unidentified path.
    priv = Ed25519PrivateKey.generate()
    await _register(gateway, ws, _did(priv))
    gateway._client_webids[ws] = ""
    monkeypatch.setattr(gateway._store, "get_relationship_by_cert_id",
                        lambda cid: {"peer_did": "did:key:zP"})
    monkeypatch.setattr(gateway._store, "mark_revoked", lambda *a, **k: None)
    called = {}
    orig = gateway.broadcast
    async def _spy(payload):
        called["hit"] = payload
        return await orig(payload)
    monkeypatch.setattr(gateway, "broadcast", _spy)
    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "revoke_contact", "cert_id": "c1"})
    assert called.get("hit", {}).get("type") == "contact_revoked"
