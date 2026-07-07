"""Pod-poll delivery scoping (the biggest flagged broadcast, PLAN §E3/R53-pod).

Polled pod entries (DM + room messages, and their link previews) used to
self.broadcast() to EVERY session on the gateway. Now they route to the entry's
participants: a DM goes to the account owning the relationship cert; a room
message goes to the room's members; broadcast remains only as the
unattributable fallback (older ownerless relationship rows — single-user-safe).
"""
from __future__ import annotations

import json
import time
import types

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import proxion_messenger_core._gateway_pod as pod_mod
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


def _got(ws, type_):
    return any(json.loads(c[0][0]).get("type") == type_ for c in ws.send.call_args_list)


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "podscope.db")),
    )


def _fake_entry(source, cert_id="cert-1", thread_id=None, content="hello pod"):
    peer = Ed25519PrivateKey.generate()
    peer_hex = peer.public_key().public_bytes_raw().hex()
    cert = types.SimpleNamespace(certificate_id=cert_id, subject=peer_hex)
    msg = types.SimpleNamespace(
        message_id="pod-msg-" + source, from_pub_hex=peer_hex, content=content,
        timestamp=time.time(), reply_to_id=None, message_type="message",
        seq_num=0, prev_hash="",
    )
    entry = types.SimpleNamespace(source=source, cert=cert, message=msg)
    if thread_id is not None:
        entry.thread_id = thread_id
    return entry


def _connect(gw, did):
    ws = _mock_ws()
    gw.clients.add(ws)
    gw._client_webids[ws] = did
    gw._webid_sockets.setdefault(did, set()).add(ws)
    return ws


async def _poll_with(gw, entries, monkeypatch):
    # RelationshipCertificate isinstance filter needs no real certs: dm_clients empty.
    monkeypatch.setattr(pod_mod, "poll_inbox", lambda *a, **k: entries)
    await gw.do_poll()


@pytest.mark.asyncio
async def test_polled_dm_goes_to_relationship_owner_only(gateway, monkeypatch):
    owner_ws = _connect(gateway, "did:key:zOwner")
    stranger_ws = _connect(gateway, "did:key:zStranger")
    # The relationship (with an owner) that the polled DM rides on.
    gateway._store.save_relationship(
        {"certificate_id": "cert-dm-1", "subject": "ab" * 32,
         "created_at": 0, "expires_at": 2**31 - 1},
        peer_did="did:key:zPeer", owner_webid="did:key:zOwner",
    )
    await _poll_with(gateway, [_fake_entry("dm", cert_id="cert-dm-1")], monkeypatch)
    assert _got(owner_ws, "message"), "the relationship owner must receive the polled DM"
    assert not _got(stranger_ws, "message"), "an unrelated session must NOT receive someone else's DM"


@pytest.mark.asyncio
async def test_polled_dm_falls_back_to_broadcast_when_ownerless(gateway, monkeypatch):
    """Older relationship rows have no owner — deliver via broadcast so a
    single-user gateway never loses messages."""
    ws = _connect(gateway, "did:key:zAnyone")
    gateway._store.save_relationship(
        {"certificate_id": "cert-dm-2", "subject": "cd" * 32,
         "created_at": 0, "expires_at": 2**31 - 1},
        peer_did="did:key:zPeer2", owner_webid="",
    )
    await _poll_with(gateway, [_fake_entry("dm", cert_id="cert-dm-2")], monkeypatch)
    assert _got(ws, "message"), "ownerless entries must still deliver (broadcast fallback)"


@pytest.mark.asyncio
async def test_polled_room_message_goes_to_members_only(gateway, monkeypatch):
    member_ws = _connect(gateway, "did:key:zMember")
    stranger_ws = _connect(gateway, "did:key:zNotInRoom")
    gateway._store.add_room_member("room-9", "did:key:zMember")
    await _poll_with(gateway, [_fake_entry("room", thread_id="room-9")], monkeypatch)
    assert _got(member_ws, "message"), "a room member must receive the polled room message"
    assert not _got(stranger_ws, "message"), "a non-member must NOT receive the room message"


@pytest.mark.asyncio
async def test_link_preview_scoped_to_recipients(gateway, monkeypatch):
    owner_ws = _connect(gateway, "did:key:zOwner")
    stranger_ws = _connect(gateway, "did:key:zStranger")
    import proxion_messenger_core._gateway_rooms  # noqa: F401 — mixin already loaded
    async def _fake_preview(url):
        return {"url": url, "title": "T"}
    monkeypatch.setattr("proxion_messenger_core.linkpreview.fetch_link_preview", _fake_preview)
    await gateway.process_link_previews(
        "see https://example.com/x", "dm", "m-1", recipients=["did:key:zOwner"])
    assert _got(owner_ws, "link_preview"), "the recipient must get the preview"
    assert not _got(stranger_ws, "link_preview"), "an unrelated session must NOT get the preview"
