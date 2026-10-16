"""Blocking someone stops your own messages to them on every send path,
including the relay path cross-gateway friends use (no pod client)."""
from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_blocked_friend_on_the_relay_path_gets_nothing(tmp_path):
    from unittest.mock import AsyncMock
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.federation import RelationshipCertificate, Capability

    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "blk.db")))
    peer = AgentState.generate()
    peer_did = pub_key_to_did(peer.identity_pub_bytes)
    cert = RelationshipCertificate(
        issuer=gw.agent.identity_pub_bytes.hex(), subject=peer.identity_pub_bytes.hex(),
        capabilities=[Capability(with_="stash://dm/", can="crud/write")])
    cert.sign(gw.agent.identity_key)
    gw._store.save_relationship(cert.to_dict(), peer_did=peer_did)
    assert cert.certificate_id not in gw.dm_clients  # relay path, not pod

    ws = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 1)
    me = pub_key_to_did(gw.agent.identity_pub_bytes)
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": me})
    await gw.process_command(ws, {"cmd": "block", "webid": peer_did})
    ws.send.reset_mock()

    async def _send(mid):
        ws.send.reset_mock()
        await gw.process_command(ws, {
            "cmd": "send_dm", "cert_id": cert.certificate_id, "message_id": mid,
            "content": "hello"})
        return [json.loads(c[0][0]) for c in ws.send.call_args_list]

    sent = await _send("m-blocked")
    assert any(e.get("message") == "Recipient is blocked" for e in sent), sent
    assert not any(e.get("type") in ("message", "dm") or e.get("message_id") == "m-blocked"
                   for e in sent), sent

    await gw.process_command(ws, {"cmd": "unblock", "webid": peer_did})
    sent = await _send("m-after")
    assert not any(e.get("message") == "Recipient is blocked" for e in sent), sent
    assert any(e.get("message_id") == "m-after" for e in sent), sent
