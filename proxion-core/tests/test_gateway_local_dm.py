"""Tests for gateway local_dm and resolve_did commands (Round 29)."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9998),
        read_state=ReadState(),
    )


@pytest.fixture
def two_clients(gateway):
    sender = MagicMock()
    sender.send = AsyncMock()
    target = MagicMock()
    target.send = AsyncMock()
    gateway.clients = {sender, target}
    gateway._client_webids[sender] = "did:key:alice"
    gateway._client_webids[target] = "did:key:bob"
    gateway._webid_sockets["did:key:alice"] = sender
    gateway._webid_sockets["did:key:bob"] = target
    return sender, target


@pytest.mark.asyncio
async def test_local_dm_delivered_to_registered_target(gateway, two_clients):
    sender, target = two_clients
    await gateway.process_command(sender, {
        "cmd": "local_dm",
        "target_webid": "did:key:bob",
        "content": "hello bob",
        "thread_id": "did:key:bob",
    })
    # Target must receive the message
    target.send.assert_called_once()
    sent = json.loads(target.send.call_args[0][0])
    assert sent["type"] == "message"
    assert sent["content"] == "hello bob"
    assert sent["from_webid"] == "did:key:alice"
    assert sent["local"] is True
    # Sender must receive echo with own=True (exactly one call — target was connected)
    sender.send.assert_called_once()
    echo = json.loads(sender.send.call_args[0][0])
    assert echo["own"] is True
    assert echo["content"] == "hello bob"


@pytest.mark.asyncio
async def test_local_dm_target_not_connected(gateway):
    sender = MagicMock()
    sender.send = AsyncMock()
    gateway.clients = {sender}
    gateway._client_webids[sender] = "did:key:alice"
    gateway._webid_sockets["did:key:alice"] = sender

    await gateway.process_command(sender, {
        "cmd": "local_dm",
        "target_webid": "did:key:nobody",
        "content": "test",
    })
    # Sender gets echo + info
    calls = [json.loads(c[0][0]) for c in sender.send.call_args_list]
    info_msgs = [c for c in calls if c.get("type") == "info"]
    assert len(info_msgs) == 1
    assert "not connected" in info_msgs[0]["message"]


@pytest.mark.asyncio
async def test_local_dm_source_is_local_dm(gateway, two_clients):
    """Event source must be 'local_dm' so the frontend sidebar preview updates correctly."""
    sender, target = two_clients
    await gateway.process_command(sender, {
        "cmd": "local_dm",
        "target_webid": "did:key:bob",
        "content": "hello",
        "thread_id": "did:key:bob",
    })
    sent = json.loads(target.send.call_args[0][0])
    assert sent["source"] == "local_dm", f"expected 'local_dm', got {sent.get('source')!r}"
    echo = json.loads(sender.send.call_args[0][0])
    assert echo["source"] == "local_dm"


@pytest.mark.asyncio
async def test_local_dm_e2e_fields_forwarded(gateway, two_clients):
    """All E2E wire fields must be forwarded to target and echoed to sender."""
    sender, target = two_clients
    await gateway.process_command(sender, {
        "cmd": "local_dm",
        "target_webid": "did:key:bob",
        "content": "<ciphertext>",
        "thread_id": "did:key:bob",
        "e2e": True,
        "nonce": "aabbcc",
        "msg_num": 3,
        "ratchet_pub": "pubpubpub",
        "pn": 2,
        "x25519_pub": "mypubkey",
    })
    for call_mock in (target, sender):
        ev = json.loads(call_mock.send.call_args[0][0])
        assert ev["e2e"] is True
        assert ev["nonce"] == "aabbcc"
        assert ev["msg_num"] == 3
        assert ev["ratchet_pub"] == "pubpubpub"
        assert ev["pn"] == 2
        assert ev["x25519_pub"] == "mypubkey"


@pytest.mark.asyncio
async def test_resolve_did_valid(gateway):
    real_agent = AgentState.generate()
    from proxion_messenger_core.didkey import agent_did
    did = agent_did(real_agent)

    ws = MagicMock()
    ws.send = AsyncMock()
    gateway.clients = {ws}
    gateway._client_webids[ws] = "did:key:testdidresolver-valid"

    await gateway.process_command(ws, {"cmd": "resolve_did", "did": did})
    ws.send.assert_called_once()
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "did_resolved"
    assert resp["did"] == did
    assert resp["webid"] == did


@pytest.mark.asyncio
async def test_resolve_did_invalid(gateway):
    ws = MagicMock()
    ws.send = AsyncMock()
    gateway.clients = {ws}
    gateway._client_webids[ws] = "did:key:testdidresolver-invalid"

    await gateway.process_command(ws, {"cmd": "resolve_did", "did": "not-a-did"})
    ws.send.assert_called_once()
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    # Schema validation or handler can catch invalid DIDs
    assert (
        "did" in resp.get("message", "").lower()
        or "resolve" in resp.get("message", "").lower()
        or resp.get("code") == "E_SCHEMA"
    )
