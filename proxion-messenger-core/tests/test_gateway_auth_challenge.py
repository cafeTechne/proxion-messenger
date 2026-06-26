import base64
import json
import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did


def _make_key_pair():
    """Return (Ed25519PrivateKey, did:key string) for a fresh identity."""
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return priv, pub_key_to_did(pub_bytes)


def _sign_nonce(priv_key: Ed25519PrivateKey, nonce: str) -> str:
    """Sign nonce bytes with Ed25519, return URL-safe base64 (no padding)."""
    sig = priv_key.sign(nonce.encode())
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    _, agent_did = _make_key_pair()
    agent.identity_pub_bytes = b"\x00" * 32
    agent.identity_key = MagicMock()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9990, db_path=str(tmp_path / "auth.db")),
        read_state=ReadState(),
    )


def _fake_ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    return ws


def _sent_types(ws):
    return [json.loads(c[0][0])["type"] for c in ws.send.call_args_list]


def _sent_msgs(ws):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list]


@pytest.mark.asyncio
async def test_auth_challenge_issued_on_register(gateway, monkeypatch):
    """When PROXION_REQUIRE_AUTH=1, register with did:key should send auth_challenge."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    priv, did = _make_key_pair()
    ws = _fake_ws()

    await gateway.process_command(ws, {"cmd": "register", "did": did})

    types = _sent_types(ws)
    assert "auth_challenge" in types, f"Expected auth_challenge, got: {types}"
    # Should NOT be registered yet
    assert "registered" not in types

    # Nonce stored in pending_auth
    assert ws in gateway._pending_auth
    assert gateway._pending_auth[ws]["nonce"]


@pytest.mark.asyncio
async def test_valid_signature_completes_registration(gateway, monkeypatch):
    """A valid auth_response with correct Ed25519 sig should result in registered."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    priv, did = _make_key_pair()
    ws = _fake_ws()

    await gateway.process_command(ws, {"cmd": "register", "did": did, "display_name": "Alice"})

    # Extract the nonce from the challenge
    challenge_msg = next(
        json.loads(c[0][0]) for c in ws.send.call_args_list
        if json.loads(c[0][0])["type"] == "auth_challenge"
    )
    nonce = challenge_msg["nonce"]
    sig = _sign_nonce(priv, nonce)

    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "auth_response", "nonce": nonce, "signature": sig})

    types = _sent_types(ws)
    assert "registered" in types, f"Expected registered after valid sig, got: {types}"
    assert ws in gateway._client_webids
    assert gateway._client_webids[ws] == did


@pytest.mark.asyncio
async def test_invalid_signature_rejected(gateway, monkeypatch):
    """A wrong signature should return auth_failed with invalid_signature."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    priv, did = _make_key_pair()
    wrong_priv = Ed25519PrivateKey.generate()
    ws = _fake_ws()

    await gateway.process_command(ws, {"cmd": "register", "did": did})
    challenge_msg = next(
        json.loads(c[0][0]) for c in ws.send.call_args_list
        if json.loads(c[0][0])["type"] == "auth_challenge"
    )
    nonce = challenge_msg["nonce"]

    # Sign with wrong key
    bad_sig = _sign_nonce(wrong_priv, nonce)
    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "auth_response", "nonce": nonce, "signature": bad_sig})

    msgs = _sent_msgs(ws)
    failed = next((m for m in msgs if m.get("type") == "auth_failed"), None)
    assert failed is not None, f"Expected auth_failed, got: {[m['type'] for m in msgs]}"
    assert failed["reason"] == "invalid_signature"
    assert ws not in gateway._client_webids


@pytest.mark.asyncio
async def test_expired_challenge_rejected(gateway, monkeypatch):
    """An auth_response arriving after the 30s window should return auth_failed."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    priv, did = _make_key_pair()
    ws = _fake_ws()

    await gateway.process_command(ws, {"cmd": "register", "did": did})
    challenge_msg = next(
        json.loads(c[0][0]) for c in ws.send.call_args_list
        if json.loads(c[0][0])["type"] == "auth_challenge"
    )
    nonce = challenge_msg["nonce"]

    # Artificially expire the challenge
    gateway._pending_auth[ws]["expires_at"] = time.time() - 1

    sig = _sign_nonce(priv, nonce)
    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "auth_response", "nonce": nonce, "signature": sig})

    msgs = _sent_msgs(ws)
    failed = next((m for m in msgs if m.get("type") == "auth_failed"), None)
    assert failed is not None, f"Expected auth_failed, got: {[m['type'] for m in msgs]}"
    assert failed["reason"] == "challenge_expired"
    assert ws not in gateway._client_webids


@pytest.mark.asyncio
async def test_webid_rejected_when_auth_required(gateway, monkeypatch):
    """When PROXION_REQUIRE_AUTH=1, a plain WebID must be rejected — not silently registered."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    ws = _fake_ws()

    await gateway.process_command(ws, {"cmd": "register", "webid": "https://alice.example.com/profile#me"})

    msgs = _sent_msgs(ws)
    failed = next((m for m in msgs if m.get("type") == "auth_failed"), None)
    assert failed is not None, f"Expected auth_failed for WebID, got: {[m.get('type') for m in msgs]}"
    assert failed["reason"] == "unsupported_identity"
    assert ws not in gateway._client_webids


@pytest.mark.asyncio
async def test_webid_allowed_when_auth_disabled(gateway, monkeypatch):
    """When PROXION_REQUIRE_AUTH=0, a plain WebID should register normally (local dev)."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    ws = _fake_ws()

    await gateway.process_command(ws, {"cmd": "register", "webid": "https://alice.example.com/profile#me"})

    msgs = _sent_msgs(ws)
    assert any(m.get("type") == "registered" for m in msgs), \
        f"Expected registered for WebID with auth disabled, got: {[m.get('type') for m in msgs]}"
    assert ws in gateway._client_webids
    assert gateway._client_webids[ws] == "https://alice.example.com/profile#me"


@pytest.mark.asyncio
async def test_wildcard_bind_requires_auth_by_default(tmp_path, monkeypatch):
    """Gateway bound to 0.0.0.0 must require auth when PROXION_REQUIRE_AUTH is unset."""
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    # GatewayConfig defaults to host="0.0.0.0", which must now require auth.
    gw = ProxionGateway(
        agent=MagicMock(spec=AgentState, identity_pub_bytes=b"\x00" * 32, identity_key=MagicMock()),
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9985, db_path=str(tmp_path / "wc.db")),
        read_state=ReadState(),
    )
    priv, did = _make_key_pair()
    ws = _fake_ws()

    await gw.process_command(ws, {"cmd": "register", "did": did})

    types = _sent_types(ws)
    assert "auth_challenge" in types, (
        f"0.0.0.0-bound gateway should require auth by default; got: {types}"
    )
    assert "registered" not in types


@pytest.mark.asyncio
async def test_loopback_bind_skips_auth_by_default(tmp_path, monkeypatch):
    """Gateway bound to 127.0.0.1 must NOT require auth when PROXION_REQUIRE_AUTH is unset."""
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    gw = ProxionGateway(
        agent=MagicMock(spec=AgentState, identity_pub_bytes=b"\x00" * 32, identity_key=MagicMock()),
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9984, host="127.0.0.1", db_path=str(tmp_path / "lb.db")),
        read_state=ReadState(),
    )
    priv, did = _make_key_pair()
    ws = _fake_ws()

    await gw.process_command(ws, {"cmd": "register", "did": did})

    types = _sent_types(ws)
    assert "registered" in types, (
        f"Loopback-bound gateway should skip auth; got: {types}"
    )
    assert "auth_challenge" not in types
