"""A DM edit must reach only the DM participants — never every gateway client.

Regression for _handle_edit_message calling self.broadcast(), which leaked a
private DM edit (message_id + new_content) to all connected clients.
"""
from __future__ import annotations

import json
import types

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
import proxion_messenger_core.messaging as messaging


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
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
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "edit.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_dm_edit_goes_to_participants_not_everyone(gateway, noauth_env, monkeypatch):
    editor = Ed25519PrivateKey.generate()
    peer = Ed25519PrivateKey.generate()
    stranger = Ed25519PrivateKey.generate()
    editor_did, peer_did = _did(editor), _did(peer)

    ws_editor = _mock_ws()
    ws_peer = _mock_ws()
    ws_stranger = _mock_ws()
    await _register(gateway, ws_editor, editor_did)
    await _register(gateway, ws_peer, peer_did)
    await _register(gateway, ws_stranger, _did(stranger))

    # A cert whose subject is the peer's ed25519 pub hex (as real certs carry).
    cert = types.SimpleNamespace(subject=peer.public_key().public_bytes_raw().hex())
    cert_id = "cert-xyz"
    gateway.dm_clients[cert_id] = (cert, object())  # pod client unused (send stubbed)

    # Stub the pod messaging layer so no real I/O happens.
    monkeypatch.setattr(messaging, "edit_message", lambda **kw: object())
    monkeypatch.setattr(messaging, "send", lambda *a, **k: None)

    for ws in (ws_editor, ws_peer, ws_stranger):
        ws.send.reset_mock()
    await gateway.process_command(ws_editor, {
        "cmd": "edit_message", "cert_id": cert_id,
        "message_id": "m-1", "content": "revised",
    })

    assert _got(ws_editor, "message_edited"), "editor should see their own edit"
    assert _got(ws_peer, "message_edited"), "the DM peer should get the edit"
    assert not _got(ws_stranger, "message_edited"), "an unrelated client must NOT see the DM edit"
