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


def _own_signed(agent, to_did, nonce, message_id):
    from proxion_messenger_core.didkey import pub_key_to_did
    from_did = pub_key_to_did(agent.identity_pub_bytes)
    payload = _signed_payload(agent, from_did, to_did, relay_nonce=nonce, message_id=message_id)
    payload["relay_nonce"] = nonce
    return payload


@pytest.mark.asyncio
async def test_relay_nonce_replay_blocked_in_memory():
    """A replayed relay is answered as a duplicate (in-memory dedup)."""
    gw, agent = _make_gateway()
    payload = _own_signed(agent, "did:key:z6Mktarget", "aabbccdd11223344", "msg-1")
    status, _ = await gw._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("202")
    status, resp = await gw._handle_relay_post(json.dumps(payload).encode())
    assert json.loads(resp).get("status") == "duplicate"


@pytest.mark.asyncio
async def test_forged_relay_does_not_burn_the_real_message_id():
    """Dedup is recorded only after the signature verifies: a forged POST that
    reuses a message id and nonce must not make the real relay a 'duplicate'."""
    gw, agent = _make_gateway()
    real = _own_signed(agent, "did:key:z6Mktarget", "aabbccdd11223344", "msg-1")
    forged = {**real, "signature": "AAAA"}
    status, _ = await gw._handle_relay_post(json.dumps(forged).encode())
    assert status.startswith("400")
    status, resp = await gw._handle_relay_post(json.dumps(real).encode())
    assert status.startswith("202"), resp


@pytest.mark.asyncio
async def test_relay_nonce_replay_blocked_across_restart_with_store(tmp_path):
    """A relay seen before a restart is still a duplicate after it (SQLite dedup)."""
    db_path = str(tmp_path / "relay.db")
    gw1, agent = _make_gateway(db_path=db_path)
    payload = _own_signed(agent, "did:key:ztarget", "deadbeef12345678", "msg-persist")
    status, _ = await gw1._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("202")

    gw2, _ = _make_gateway(db_path=db_path)
    gw2._seen_relay_ids.clear()
    gw2._seen_relay_nonces.clear()
    status, resp = await gw2._handle_relay_post(json.dumps(payload).encode())
    assert json.loads(resp).get("status") == "duplicate"
