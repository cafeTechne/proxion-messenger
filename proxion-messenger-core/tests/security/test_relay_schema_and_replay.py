"""Round 1: Relay payload schema bounds and SQLite-backed nonce replay tests."""
import asyncio
import json
import time
import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import sign_relay_message


def _make_gateway(db_path=None):
    agent = AgentState.generate()
    cfg = GatewayConfig(port=9976, db_path=db_path)
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=cfg, read_state=ReadState(),
    )
    return gw, agent


def _signed_payload(agent, from_did, to_did, content="hello", relay_nonce="", message_id=None):
    import uuid
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mid = message_id or str(uuid.uuid4())
    sig = sign_relay_message(
        agent.identity_key, from_did, to_did, mid, content, ts, relay_nonce=relay_nonce
    )
    return {
        "from_webid": from_did,
        "to_webid": to_did,
        "message_id": mid,
        "content": content,
        "timestamp": ts,
        "signature": sig,
    }


@pytest.mark.asyncio
async def test_relay_rejects_oversized_content():
    gw, agent = _make_gateway()
    from_did = f"did:key:{agent.identity_pub_bytes.hex()[:10]}"
    to_did = "did:key:zother"
    big_content = "x" * 17000  # > 16 KiB
    payload = _signed_payload(agent, from_did, to_did, content=big_content)
    body = json.dumps(payload).encode()
    status, resp = await gw._handle_relay_post(body)
    assert status.startswith("400"), f"Expected 400, got {status}"
    data = json.loads(resp)
    assert "content_too_large" in data.get("error", "")


@pytest.mark.asyncio
async def test_relay_rejects_oversized_message_id():
    gw, agent = _make_gateway()
    from_did = f"did:key:{agent.identity_pub_bytes.hex()[:10]}"
    to_did = "did:key:zother"
    payload = _signed_payload(agent, from_did, to_did, message_id="x" * 129)
    body = json.dumps(payload).encode()
    status, resp = await gw._handle_relay_post(body)
    assert status.startswith("400"), f"Expected 400, got {status}"
    data = json.loads(resp)
    assert "message_id_too_long" in data.get("error", "")


@pytest.mark.asyncio
async def test_relay_rejects_unknown_payload_keys():
    gw, agent = _make_gateway()
    from_did = f"did:key:{agent.identity_pub_bytes.hex()[:10]}"
    to_did = "did:key:zother"
    payload = _signed_payload(agent, from_did, to_did)
    payload["totally_unknown_field"] = "injected"
    body = json.dumps(payload).encode()
    status, resp = await gw._handle_relay_post(body)
    assert status.startswith("400"), f"Expected 400, got {status}"
    data = json.loads(resp)
    assert "unknown_relay_fields" in data.get("error", "")


@pytest.mark.asyncio
async def test_relay_rejects_oversized_file_attachment():
    """File relay is allowed, but files exceeding 128 KiB base64 are rejected (413)."""
    import base64
    gw, agent = _make_gateway()
    from_did = f"did:key:{agent.identity_pub_bytes.hex()[:10]}"
    to_did = "did:key:zother"
    payload = _signed_payload(agent, from_did, to_did)
    huge_b64 = base64.b64encode(b"x" * 100000).decode()  # ~133 KiB base64
    payload["file"] = {"filename": "big.bin", "mime_type": "text/plain",
                       "size": 100000, "data_b64": huge_b64}
    body = json.dumps(payload).encode()
    status, resp = await gw._handle_relay_post(body)
    assert status.startswith("413"), f"Expected 413, got {status}"
    data = json.loads(resp)
    assert "file_too_large_for_relay" in data.get("error", "")


@pytest.mark.asyncio
async def test_relay_nonce_replay_blocked_in_memory():
    """Same nonce replayed twice → second is rejected (in-memory dedup)."""
    gw, agent = _make_gateway()
    from_did = "did:key:z6MktestreplayA"
    to_did = "did:key:z6Mktarget"
    nonce = "aabbccdd11223344"

    # First submission (will fail signature but nonce is recorded in in-memory)
    payload = {
        "from_webid": from_did, "to_webid": to_did,
        "message_id": "msg-1", "content": "hello",
        "timestamp": "2030-01-01T00:00:00Z", "signature": "AAAA",
        "relay_nonce": nonce,
    }
    await gw._handle_relay_post(json.dumps(payload).encode())
    # Second submission with same nonce → duplicate
    payload2 = dict(payload)
    payload2["message_id"] = "msg-2"
    status, resp = await gw._handle_relay_post(json.dumps(payload2).encode())
    data = json.loads(resp)
    assert data.get("status") == "duplicate"


@pytest.mark.asyncio
async def test_relay_nonce_replay_blocked_across_restart_with_store(tmp_path):
    """Nonce seen before store restart is rejected after restart (SQLite dedup)."""
    db_path = str(tmp_path / "relay.db")
    gw1, agent = _make_gateway(db_path=db_path)

    from_did = "did:key:z6MktestreplayB"
    nonce = "deadbeef12345678"

    # Record the nonce through the first gateway instance
    gw1._store.record_relay_nonce(
        __import__("hashlib").sha256(f"{from_did}:{nonce}".encode()).hexdigest()
    )

    # Create a new gateway instance with the same DB — nonce should be seen
    gw2, _ = _make_gateway(db_path=db_path)
    gw2.agent = agent  # same agent

    payload = {
        "from_webid": from_did, "to_webid": "did:key:ztarget",
        "message_id": "msg-persist", "content": "hi",
        "timestamp": "2030-01-01T00:00:00Z", "signature": "AAAA",
        "relay_nonce": nonce,
    }
    status, resp = await gw2._handle_relay_post(json.dumps(payload).encode())
    data = json.loads(resp)
    assert data.get("status") == "duplicate"
