"""Server-side thread mute suppresses OFFLINE web push.

Mute is otherwise client-side localStorage the gateway can't see, so a muted
thread still pushed to your phone when the app was closed.
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


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "mute.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def test_store_roundtrip(gateway):
    s = gateway._store
    assert s.is_thread_muted("did:alice", "did:bob") is False
    s.set_thread_mute("did:alice", "did:bob", True)
    assert s.is_thread_muted("did:alice", "did:bob") is True
    assert "did:bob" in s.get_muted_keys("did:alice")
    s.set_thread_mute("did:alice", "did:bob", False)
    assert s.is_thread_muted("did:alice", "did:bob") is False


@pytest.mark.asyncio
async def test_set_thread_mute_handler_persists(gateway, noauth_env):
    alice = _did(Ed25519PrivateKey.generate())
    ws = _mock_ws()
    await _register(gateway, ws, alice)
    await gateway.process_command(ws, {"cmd": "set_thread_mute", "mute_key": "did:key:zBob", "muted": True})
    assert gateway._store.is_thread_muted(alice, "did:key:zBob") is True
    await gateway.process_command(ws, {"cmd": "set_thread_mute", "mute_key": "did:key:zBob", "muted": False})
    assert gateway._store.is_thread_muted(alice, "did:key:zBob") is False


@pytest.mark.asyncio
async def test_dm_push_skipped_when_muted(gateway, noauth_env, monkeypatch):
    """An offline recipient who muted the sender gets no push; unmuted → push."""
    sends = []
    import proxion_messenger_core.webpush as webpush
    monkeypatch.setattr(webpush, "send_web_push", lambda **kw: sends.append(kw))
    gateway._vapid_private_pem = "pem"
    gateway._vapid_subject = "mailto:x@y"

    alice = _did(Ed25519PrivateKey.generate())  # sender (online)
    bob = _did(Ed25519PrivateKey.generate())    # recipient (OFFLINE: push sub, no socket)
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, alice)
    gateway._store.save_push_subscription("sub-bob", bob, "https://push/bob", "p256", "auth")

    # Bob muted Alice → no push.
    gateway._store.set_thread_mute(bob, alice, True)
    await gateway.process_command(ws_alice, {"cmd": "local_dm", "target_webid": bob, "content": "hi"})
    assert not sends, "muted recipient must NOT be pushed"

    # Bob unmutes → push resumes.
    gateway._store.set_thread_mute(bob, alice, False)
    await gateway.process_command(ws_alice, {"cmd": "local_dm", "target_webid": bob, "content": "hi again"})
    assert sends, "unmuted recipient should be pushed"
