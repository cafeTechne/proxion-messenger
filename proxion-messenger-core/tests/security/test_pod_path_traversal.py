"""Pod path-traversal defenses for the local-DM write-through (F1).

``message_id`` on a ``local_dm`` is client-supplied and flows into the pod
write-through path ``stash://pod/local_dms/<thread_key>/<message_id>.json``. A
crafted id such as ``../../rooms/victim/messages/evil`` would otherwise escape
its container and let a client write an arbitrary resource on the operator's pod
with the operator's DPoP credentials.

Defense is layered:
  * the command schema rejects a traversal ``message_id`` before dispatch,
  * ``_handle_local_dm`` re-validates the id (covers the send_dm→local_dm path),
  * ``SolidResolver.resolve`` refuses any ``..``-escape as a central backstop,
  * ``PodRoomStore`` validates each id segment it embeds in a URI.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.solid import SolidResolver, SolidResolverError
from proxion_messenger_core.pod_room_store import PodRoomStore
from proxion_messenger_core.command_validation import (
    validate_command_payload,
    SchemaError,
)


# ── resolver guard ────────────────────────────────────────────────────────────

def test_resolver_resolves_normal_nested_path():
    r = SolidResolver("https://operator.example/")
    url = r.resolve("stash://pod/local_dms/abc123def456/local-deadbeef00.json")
    assert url == "https://operator.example/local_dms/abc123def456/local-deadbeef00.json"


def test_resolver_rejects_dotdot_escape():
    r = SolidResolver("https://operator.example/")
    with pytest.raises(SolidResolverError):
        r.resolve("stash://pod/local_dms/abc123/../../../etc/passwd")


def test_resolver_rejects_cross_container_write_that_stays_on_host():
    # The real F1 payload: it normalizes to a resource still under the pod base
    # (same host), so a bare startswith check would pass. The ``..`` segment must
    # itself be refused.
    r = SolidResolver("https://operator.example/")
    escape = "stash://pod/local_dms/hash16/../../rooms/victim/messages/evil.json"
    with pytest.raises(SolidResolverError):
        r.resolve(escape)


# ── command schema backstop ───────────────────────────────────────────────────

def test_schema_rejects_traversal_local_dm_message_id():
    with pytest.raises(SchemaError):
        validate_command_payload("local_dm", {
            "target_webid": "did:key:zPeer",
            "content": "hi",
            "message_id": "../../rooms/victim/messages/evil",
        })


def test_schema_rejects_traversal_send_dm_message_id():
    with pytest.raises(SchemaError):
        validate_command_payload("send_dm", {
            "cert_id": "c1",
            "content": "hi",
            "message_id": "../escape",
        })


def test_schema_rejects_leading_dot_message_id():
    with pytest.raises(SchemaError):
        validate_command_payload("local_dm", {
            "target_webid": "did:key:zPeer",
            "content": "hi",
            "message_id": ".hidden",
        })


@pytest.mark.parametrize("mid", [
    "local-deadbeef0011",
    "550e8400-e29b-41d4-a716-446655440000",
    "msg-abc123",
    "client-chosen-id-123",
])
def test_schema_allows_legitimate_message_ids(mid):
    # Absent/empty id and every real id format must pass unchanged.
    validate_command_payload("local_dm", {
        "target_webid": "did:key:zPeer", "content": "hi", "message_id": mid,
    })
    validate_command_payload("local_dm", {
        "target_webid": "did:key:zPeer", "content": "hi",
    })


# ── PodRoomStore segment guard ────────────────────────────────────────────────

def test_pod_room_store_message_uri_normal():
    store = PodRoomStore(client=object())
    uri = store._message_uri("room-1", "local-abc123")
    assert uri == "stash://pod/rooms/room-1/messages/local-abc123.json"


def test_pod_room_store_message_uri_rejects_traversal():
    store = PodRoomStore(client=object())
    with pytest.raises(ValueError):
        store._message_uri("room-1", "../../evil")
    with pytest.raises(ValueError):
        store._message_uri("../../victim", "m1")


# ── live handler behavior ─────────────────────────────────────────────────────

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


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "trav.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def _sent(ws):
    return [json.loads(c.args[0]) for c in ws.send.call_args_list]


@pytest.mark.asyncio
async def test_local_dm_traversal_id_rejected_and_no_pod_write(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    synced: list = []

    async def _fake_sync(thread_id, message):
        synced.append((thread_id, message.get("message_id")))

    monkeypatch.setattr(gateway, "_sync_local_dm_to_pod", _fake_sync)

    sender = _mock_ws()
    await _register(gateway, sender, _did(Ed25519PrivateKey.generate()))
    sender.send.reset_mock()

    await gateway.process_command(sender, {
        "cmd": "local_dm",
        "target_webid": "did:key:zPeer",
        "content": "pwn",
        "message_id": "../../rooms/victim/messages/evil",
    })

    types = [m.get("type") for m in _sent(sender)]
    assert "error" in types
    # Nothing was written through to the pod on a traversal id.
    assert synced == []


@pytest.mark.asyncio
async def test_handle_local_dm_guards_direct_traversal_id(gateway, monkeypatch):
    # The send_dm→_handle_local_dm path calls the handler directly, bypassing the
    # dispatcher schema check, so the handler must guard the id itself.
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    synced: list = []

    async def _fake_sync(thread_id, message):
        synced.append((thread_id, message.get("message_id")))

    monkeypatch.setattr(gateway, "_sync_local_dm_to_pod", _fake_sync)

    sender = _mock_ws()
    await _register(gateway, sender, _did(Ed25519PrivateKey.generate()))
    sender.send.reset_mock()

    await gateway._handle_local_dm(sender, {
        "target_webid": "did:key:zPeer",
        "content": "pwn",
        "message_id": "../../rooms/victim/evil",
    })

    types = [m.get("type") for m in _sent(sender)]
    assert "error" in types
    assert synced == []


@pytest.mark.asyncio
async def test_local_dm_legitimate_id_still_persists(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    synced: list = []

    async def _fake_sync(thread_id, message):
        synced.append((thread_id, message.get("message_id")))

    monkeypatch.setattr(gateway, "_sync_local_dm_to_pod", _fake_sync)

    sender = _mock_ws()
    sender_did = _did(Ed25519PrivateKey.generate())
    await _register(gateway, sender, sender_did)
    sender.send.reset_mock()

    await gateway.process_command(sender, {
        "cmd": "local_dm",
        "target_webid": "did:key:zPeer",
        "content": "hello",
        "message_id": "local-cafebabe0011",
        "thread_id": "did:key:zPeer",
    })

    # The pod write-through is fired via asyncio.create_task; yield so it runs.
    await asyncio.sleep(0)

    # Delivered/echoed to the sender and persisted to SQLite under the real id.
    assert gateway._store.get_message_sender("local-cafebabe0011") == sender_did
    assert synced == [("did:key:zPeer", "local-cafebabe0011")]
