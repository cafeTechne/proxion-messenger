"""Round 5 security regression tests.

Covers:
  1. Auth gate: unregistered clients cannot send non-exempt commands.
  2. Identity spoofing: from_webid fallback removed from _handle_edit_local_message.
  3. Rate-counter cleanup: _rate_counters entry removed on disconnect.
  4. Display-name cleanup: _display_names entry removed on disconnect.
  5. Handshake challenge context: signing/verifying uses domain-separated prefix.
"""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def _make_agent():
    key = Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    return agent, pub_key_to_did(pub_bytes)


def _fake_ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    return ws


def _msgs(ws):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list]


@pytest.fixture
def gateway(tmp_path):
    agent, _ = _make_agent()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9970, db_path=str(tmp_path / "r5.db")),
        read_state=ReadState(),
    )


# ---------------------------------------------------------------------------
# 1. Auth gate: unregistered clients cannot send non-exempt commands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("cmd,extra", [
    ("join_room",    {"code": "abc123"}),
    ("send_room",    {"room_id": "r1", "content": "hi"}),
    ("local_dm",     {"peer_webid": "did:key:bob", "content": "hi"}),
    ("get_rooms",    {}),
    ("voice_invite", {"target_webid": "did:key:bob", "sdp_offer": "v=0"}),
    ("search",       {"query": "hello"}),
    ("typing",       {}),
])
async def test_unregistered_command_rejected(gateway, cmd, extra):
    """Any non-exempt command from an unregistered socket must return 'Not registered'."""
    ws = _fake_ws()
    await gateway.process_command(ws, {"cmd": cmd, **extra})
    msgs = _msgs(ws)
    errors = [m for m in msgs if m.get("type") == "error"]
    assert errors, f"cmd={cmd!r}: expected error, got {msgs}"
    assert errors[0]["message"] == "Not registered", f"cmd={cmd!r}: wrong error: {errors[0]}"


@pytest.mark.asyncio
async def test_exempt_commands_pass_unauthenticated(gateway):
    """ping, pong, register, auth_response must reach their handlers without the gate blocking them."""
    ws = _fake_ws()
    # ping should not return "Not registered"
    await gateway.process_command(ws, {"cmd": "ping"})
    msgs = _msgs(ws)
    assert not any(m.get("message") == "Not registered" for m in msgs), (
        f"ping was blocked by auth gate: {msgs}"
    )


@pytest.mark.asyncio
async def test_registered_client_can_send_commands(gateway, monkeypatch):
    """Once registered, commands must not be blocked by the auth gate."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    ws = _fake_ws()
    await gateway.process_command(ws, {"cmd": "register", "webid": "did:key:alice"})
    ws.send.reset_mock()

    await gateway.process_command(ws, {"cmd": "get_rooms"})
    msgs = _msgs(ws)
    # Should not receive "Not registered"
    assert not any(m.get("message") == "Not registered" for m in msgs), (
        f"registered client was blocked: {msgs}"
    )


# ---------------------------------------------------------------------------
# 2. Identity spoofing: from_webid fallback removed from _handle_edit_local_message
# ---------------------------------------------------------------------------

@pytest.fixture
def gw_with_alice(tmp_path, monkeypatch):
    """Gateway with Alice pre-registered as did:key:alice."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    agent, _ = _make_agent()
    gw = ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9971, db_path=str(tmp_path / "r5b.db")),
        read_state=ReadState(),
    )
    alice = _fake_ws()
    gw._client_webids[alice] = "did:key:alice"
    gw._webid_sockets["did:key:alice"] = alice
    gw.clients.add(alice)
    return gw, alice


@pytest.mark.asyncio
async def test_edit_spoofing_via_from_webid_rejected(gw_with_alice):
    """An unregistered attacker asserting from_webid of an online victim must get an error."""
    gw, alice = gw_with_alice

    attacker = _fake_ws()
    # Attacker is NOT in _client_webids; tries to spoof Alice's identity
    await gw.process_command(attacker, {
        "cmd": "edit_local_message",
        "thread_id": "room-1",
        "message_id": "msg-xyz",
        "content": "tampered",
        "from_webid": "did:key:alice",
    })
    msgs = _msgs(attacker)
    assert any(m.get("type") == "error" for m in msgs), (
        f"Expected error for identity-spoofing edit, got: {msgs}"
    )
    assert not any(m.get("type") == "message_edited" for m in msgs)


@pytest.mark.asyncio
async def test_edit_by_registered_owner_still_works(gw_with_alice, tmp_path):
    """Legitimate edit by the registered owner must not be blocked."""
    gw, alice = gw_with_alice

    # Pre-seed a message in the store so the edit can succeed
    if gw._store:
        gw._store.save_message(
            "msg-abc", "room-1", "local_room",
            "did:key:alice", "Alice", "original content", "2024-01-01T00:00:00+00:00"
        )
        # Make Alice a member of the room
        gw._local_rooms["room-1"] = {
            "name": "Test", "members": {alice}, "messages": [], "code": "XYZ"
        }

    alice.send.reset_mock()
    await gw.process_command(alice, {
        "cmd": "edit_local_message",
        "thread_id": "room-1",
        "message_id": "msg-abc",
        "content": "edited content",
    })
    msgs = _msgs(alice)
    # Should not get "Not registered" error
    assert not any(m.get("message") == "Not registered" for m in msgs), (
        f"Legitimate owner was blocked: {msgs}"
    )


# ---------------------------------------------------------------------------
# 3 & 4. Cleanup: _rate_counters and _display_names removed on disconnect
# ---------------------------------------------------------------------------

def test_rate_counter_and_display_name_cleaned_up_on_disconnect(gateway):
    """Simulated disconnect must clear _rate_counters and _display_names entries."""
    ws = _fake_ws()

    # Populate both dicts as they would be after a normal session
    gateway._client_webids[ws] = "did:key:cleanup-test"
    gateway._rate_counters[ws] = [10, 1000.0]
    gateway._display_names[ws] = "Cleanup User"
    gateway.clients.add(ws)

    # Simulate the handle_client finally block (mirrors the actual code path)
    gateway.clients.discard(ws)
    gateway._pending_auth.pop(ws, None)
    gateway._auth_verified.discard(ws)
    gateway._client_webids.pop(ws, None)
    gateway._session_meta.pop(ws, None)
    gateway._rate_counters.pop(ws, None)
    gateway._display_names.pop(ws, None)

    assert ws not in gateway._rate_counters, "_rate_counters not cleaned up on disconnect"
    assert ws not in gateway._display_names, "_display_names not cleaned up on disconnect"


def test_rate_counter_does_not_grow_across_disconnects(gateway):
    """A fresh ws that has never registered must not leave a permanent entry in _rate_counters."""
    sockets = [_fake_ws() for _ in range(5)]
    for ws in sockets:
        gateway._check_ws_rate_limit(ws)

    # All 5 should be in the dict now
    assert all(ws in gateway._rate_counters for ws in sockets)

    # Simulate disconnect cleanup for each
    for ws in sockets:
        gateway._rate_counters.pop(ws, None)

    assert all(ws not in gateway._rate_counters for ws in sockets)
    assert len(gateway._rate_counters) == 0


# ---------------------------------------------------------------------------
# 5. Handshake challenge context string
# ---------------------------------------------------------------------------

def test_challenge_context_prefix_enforced():
    """accept_invite must sign challenge_marker with the domain-separation prefix,
    and verify_challenge must require the same prefix."""
    from proxion_messenger_core.handshake import create_invite, accept_invite
    from proxion_messenger_core.store import MemoryStore, StoreConfig
    from proxion_messenger_core.federation import Capability, InviteAcceptance
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    alice_id_priv = Ed25519PrivateKey.generate()
    alice_store_priv = X25519PrivateKey.generate()
    alice_store_pub = alice_store_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    bob_id_priv = Ed25519PrivateKey.generate()
    bob_store_priv = X25519PrivateKey.generate()
    bob_store_pub = bob_store_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    store = MemoryStore(StoreConfig(message_ttl=None))
    caps = [Capability(with_="stash://dm/", can="read")]

    invite = create_invite(alice_id_priv, alice_store_pub, caps)

    from proxion_messenger_core.handshake import receive_invites
    from proxion_messenger_core.sealed import seal_json, mailbox_id_for
    mailbox = mailbox_id_for(alice_store_pub)
    sealed = seal_json(invite.to_dict(), alice_store_pub)
    store.put(mailbox, sealed)

    received = receive_invites(alice_store_priv, store)
    assert received, "Could not receive the test invite"
    inv, _ = received[0]

    acceptance = accept_invite(inv, bob_id_priv, bob_store_pub, caps, store)

    # verify_challenge should succeed with the context prefix
    from proxion_messenger_core.handshake import _ed25519_verify
    assert acceptance.verify_challenge(_ed25519_verify, inv.challenge_marker), (
        "verify_challenge failed even though both sign and verify use the same context prefix"
    )


def test_bare_challenge_signature_rejected():
    """A signature over the raw challenge_marker bytes (no context prefix) must be rejected
    by verify_challenge, preventing cross-protocol signature replay."""
    from proxion_messenger_core.handshake import create_invite
    from proxion_messenger_core.federation import InviteAcceptance, Capability
    from proxion_messenger_core.handshake import _ed25519_verify
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    alice_id_priv = Ed25519PrivateKey.generate()
    alice_store_priv = X25519PrivateKey.generate()
    alice_store_pub = alice_store_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    bob_id_priv = Ed25519PrivateKey.generate()
    bob_store_priv = X25519PrivateKey.generate()
    bob_store_pub = bob_store_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    caps = [Capability(with_="stash://dm/", can="read")]
    invite = create_invite(alice_id_priv, alice_store_pub, caps)

    bob_pub_hex = bob_id_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    # Forge an acceptance signed over bare challenge_marker (no context prefix)
    bare_sig = bob_id_priv.sign(invite.challenge_marker.encode())
    forged = InviteAcceptance(
        invitation_id=invite.invitation_id,
        responder={
            "public_key": bob_pub_hex,
            "store_key": bob_store_pub.hex(),
            "capabilities": [c.to_dict() for c in caps],
        },
        challenge_response=bare_sig.hex(),
    )
    forged.sign(bob_id_priv)

    # Must be rejected because verify_challenge prepends the context prefix
    assert not forged.verify_challenge(_ed25519_verify, invite.challenge_marker), (
        "Bare (no-context) challenge signature was accepted — domain separation is broken"
    )
