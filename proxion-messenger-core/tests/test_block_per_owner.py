"""Per-owner blocklist: one user's block must not silence a peer for everyone.

Covers the store isolation + the global-file union bookkeeping. The send-path
enforcement uses store.is_blocked_by(sender, target); the legacy global file is
kept as the union for the not-yet-scoped receive path (PLAN_ROUND_51 §E4).
"""
from __future__ import annotations

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
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "block.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def test_store_per_owner_isolation(gateway):
    s = gateway._store
    assert s.is_blocked_by("A", "X") is False
    s.set_block("A", "X", True)
    assert s.is_blocked_by("A", "X") is True
    assert s.is_blocked_by("B", "X") is False  # B is unaffected by A's block
    assert "X" in s.get_blocked_by("A")
    s.set_block("A", "X", False)
    assert s.is_blocked_by("A", "X") is False


@pytest.mark.asyncio
async def test_global_file_stays_the_union(gateway, monkeypatch):
    """The legacy global file is the union of all owners' blocks: it drops X only
    when the LAST owner blocking X unblocks."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    wsA, wsB = _mock_ws(), _mock_ws()
    a, b = _did(Ed25519PrivateKey.generate()), _did(Ed25519PrivateKey.generate())
    await _register(gateway, wsA, a)
    await _register(gateway, wsB, b)

    await gateway.process_command(wsA, {"cmd": "block", "webid": "X"})
    await gateway.process_command(wsB, {"cmd": "block", "webid": "X"})
    assert gateway.blocklist.is_blocked("X")  # union has X

    await gateway.process_command(wsA, {"cmd": "unblock", "webid": "X"})
    # B still blocks X → global file must retain X.
    assert gateway.blocklist.is_blocked("X"), "global union dropped X while B still blocks it"
    assert gateway._store.is_blocked_by(a, "X") is False
    assert gateway._store.is_blocked_by(b, "X") is True

    await gateway.process_command(wsB, {"cmd": "unblock", "webid": "X"})
    assert not gateway.blocklist.is_blocked("X")  # last owner unblocked → union clears


@pytest.mark.asyncio
async def test_send_blocked_is_per_owner(gateway, monkeypatch):
    """A blocking the peer must not stop B from DMing the same peer."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    import types
    import proxion_messenger_core.messaging as messaging

    peer = Ed25519PrivateKey.generate()
    peer_hex = peer.public_key().public_bytes_raw().hex()
    a = _did(Ed25519PrivateKey.generate())
    b = _did(Ed25519PrivateKey.generate())
    wsA, wsB = _mock_ws(), _mock_ws()
    await _register(gateway, wsA, a)
    await _register(gateway, wsB, b)

    cert = types.SimpleNamespace(subject=peer_hex)
    cert_id = "cert-peer"
    gateway.dm_clients[cert_id] = (cert, object())
    sends = []
    monkeypatch.setattr(messaging, "compose", lambda *a, **k: object())
    monkeypatch.setattr(messaging, "send", lambda *a, **k: sends.append(1))

    # A blocks the peer (by the cert subject, as the UI does).
    await gateway.process_command(wsA, {"cmd": "block", "webid": peer_hex})

    wsA.send.reset_mock()
    await gateway.process_command(wsA, {"cmd": "send_dm", "cert_id": cert_id, "content": "hi"})
    assert any("blocked" in str(c[0][0]).lower() for c in wsA.send.call_args_list), \
        "A blocked the peer → A's send must be refused"

    # B never blocked the peer → B's send goes through.
    before = len(sends)
    await gateway.process_command(wsB, {"cmd": "send_dm", "cert_id": cert_id, "content": "hi"})
    assert len(sends) == before + 1, "B did not block the peer → B's DM must send"


@pytest.mark.asyncio
async def test_list_blocks_returns_owner_blocklist(gateway, monkeypatch):
    """R65: list_blocks returns the caller-owner's current block list so the
    client can render block state + a manage-list."""
    import json
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    priv = Ed25519PrivateKey.generate()
    ws = _mock_ws()
    await _register(gateway, ws, _did(priv))

    await gateway.process_command(ws, {"cmd": "block", "webid": "did:key:zEvil"})
    await gateway.process_command(ws, {"cmd": "block", "webid": "did:key:zSpam"})
    ws.send.reset_mock()

    await gateway.process_command(ws, {"cmd": "list_blocks"})
    payloads = [json.loads(c.args[0]) for c in ws.send.call_args_list]
    blocks_msg = next(p for p in payloads if p.get("type") == "blocks")
    assert set(blocks_msg["webids"]) == {"did:key:zEvil", "did:key:zSpam"}

    await gateway.process_command(ws, {"cmd": "unblock", "webid": "did:key:zEvil"})
    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "list_blocks"})
    payloads = [json.loads(c.args[0]) for c in ws.send.call_args_list]
    blocks_msg = next(p for p in payloads if p.get("type") == "blocks")
    assert set(blocks_msg["webids"]) == {"did:key:zSpam"}
