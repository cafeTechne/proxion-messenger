"""E2E-sealed DM edits, reactions and files: the gateway forwards the sealed
header and ciphertext unchanged, never needs (or gets) plaintext, validates the
header shape, and keeps rooms (not E2E) and older plaintext events working.
See docs/E2E_ENCRYPTION.md and web/e2e-secondary.js."""
from __future__ import annotations

import base64
import json
import os

import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.command_validation import sealed_fields, sealed_header
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

ALICE = "did:key:zAliceSealed"
BOB = "did:key:zBobSealed"
THREAD = "dm-thread-sealed"

# Stand-ins for browser output: base64url ciphertext and ratchet header.
CT = "q3J0Y2lwaGVydGV4dC1ub3QtcmVhbGx5LWVuY3J5cHRlZA"
SEALED = {"e2e": True, "e2e_v": 1, "content": CT, "nonce": "bm9uY2Vub25jZW5v",
          "msg_num": 3, "pn": 1, "ratchet_pub": "UkFUQ0hFVF9QVUJfS0VZXzMyX0JZVEVTX0xPTkc"}
HDR = {k: v for k, v in SEALED.items() if k != "content"}


@pytest.fixture(autouse=True)
def _noauth(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gw(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    g = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9993, db_path=str(tmp_path / "s.db")),
        read_state=ReadState(),
    )
    g._store = LocalStore(str(tmp_path / "s.db"))
    return g


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


def _connect(gw, did):
    ws = _ws()
    gw.clients.add(ws)
    gw._client_webids[ws] = did
    gw._webid_sockets.setdefault(did, set()).add(ws)
    return ws


def _events(ws, type_=None):
    out = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    return [e for e in out if type_ is None or e.get("type") == type_]


def _local_dm(gw):
    a, b = _connect(gw, ALICE), _connect(gw, BOB)
    gw._store.save_dm_thread(THREAD, BOB, "Bob", owner_webid=ALICE)
    return a, b


# ── header validation ─────────────────────────────────────────────────────────

def test_sealed_fields_plain_command_has_none():
    assert sealed_fields({"content": "hello"}) == {}


def test_sealed_fields_accepts_v1_header():
    assert sealed_fields(dict(SEALED)) == SEALED
    assert sealed_header(SEALED) == HDR


@pytest.mark.parametrize("bad", [
    {"e2e_v": 2},                          # unknown version
    {"e2e_v": True},                       # bool is not a version
    {"e2e": "yes"},                        # flag must be the JSON true
    {"content": "has spaces and !"},       # not base64url
    {"content": ""},
    {"nonce": "x" * 33},
    {"ratchet_pub": None},
    {"msg_num": -1},
    {"msg_num": "3"},
    {"pn": 2 ** 31},
])
def test_sealed_fields_rejects_malformed(bad):
    assert sealed_fields({**SEALED, **bad}) is None


def test_sealed_fields_content_cap():
    assert sealed_fields({**SEALED, "content": "A" * 1025}, max_content=1024) is None


# ── edits ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sealed_local_dm_edit_forwards_header_to_peer(gw):
    a, b = _local_dm(gw)
    await gw._handle_edit_local_message(a, {
        "message_id": "m-1", "thread_id": THREAD, **SEALED,
    })
    ev = _events(b, "message_edited")[-1]
    assert ev["new_content"] == CT
    assert ev["from_webid"] == ALICE
    for k, v in HDR.items():
        assert ev[k] == v
    # The editor's own sessions get the same sealed event (to match its outbox).
    assert _events(a, "message_edited")[-1]["ratchet_pub"] == SEALED["ratchet_pub"]


@pytest.mark.asyncio
async def test_plain_local_dm_edit_still_works(gw):
    a, b = _local_dm(gw)
    await gw._handle_edit_local_message(a, {
        "message_id": "m-2", "thread_id": THREAD, "content": "fixed typo",
    })
    ev = _events(b, "message_edited")[-1]
    assert ev["new_content"] == "fixed typo"
    assert "e2e" not in ev


@pytest.mark.asyncio
async def test_malformed_sealed_edit_rejected(gw):
    a, b = _local_dm(gw)
    await gw._handle_edit_local_message(a, {
        "message_id": "m-3", "thread_id": THREAD, **SEALED, "e2e_v": 9,
    })
    assert _events(a, "error")[-1]["message"] == "invalid_e2e_fields"
    assert not _events(b, "message_edited")


@pytest.mark.asyncio
async def test_sealed_edit_in_room_rejected(gw):
    a = _connect(gw, ALICE)
    gw._local_rooms["room-s"] = {"name": "R", "code": "x", "members": {a}}
    await gw._handle_edit_local_message(a, {"message_id": "m-4", "thread_id": "room-s", **SEALED})
    assert _events(a, "error")[-1]["message"] == "invalid_e2e_fields"


@pytest.mark.asyncio
async def test_sealed_cert_dm_edit_relays_header(gw):
    a = _connect(gw, ALICE)
    gw._store.save_relationship(
        {"certificate_id": "cert-s", "subject": "ab" * 32, "created_at": 0, "expires_at": 2 ** 31 - 1},
        peer_did=BOB, owner_webid="")
    gw._peer_gateway_urls[BOB] = "https://bob.example"
    gw._relay_ephemeral = AsyncMock()
    await gw._handle_edit_message(a, {"cert_id": "cert-s", "message_id": "m-5", **SEALED})
    import asyncio
    await asyncio.sleep(0)
    payload = gw._relay_ephemeral.call_args[0][1]
    assert payload["content_type"] == "dm_edit"
    assert payload["new_content"] == CT
    for k, v in HDR.items():
        assert payload[k] == v
    echo = _events(a, "message_edited")[-1]
    assert echo["e2e_v"] == 1 and echo["from_webid"] == ALICE


@pytest.mark.asyncio
async def test_inbound_sealed_edit_relay_delivers_header(gw):
    bob = _connect(gw, BOB)
    gw._authorized_relationship = lambda did: ({}, "our-cert")
    gw._store.get_relationship_owner = lambda did: BOB
    status, _ = await gw._handle_dm_edit_relay({
        "from_webid": ALICE, "to_webid": BOB, "message_id": "m-6", "new_content": CT, **HDR,
    })
    assert status.startswith("200")
    ev = _events(bob, "message_edited")[-1]
    assert ev["new_content"] == CT and ev["from_webid"] == ALICE
    for k, v in HDR.items():
        assert ev[k] == v


@pytest.mark.asyncio
async def test_inbound_malformed_sealed_edit_relay_rejected(gw):
    gw._authorized_relationship = lambda did: ({}, "our-cert")
    status, _ = await gw._handle_dm_edit_relay({
        "from_webid": ALICE, "to_webid": BOB, "message_id": "m-7", "new_content": CT,
        **HDR, "ratchet_pub": "not base64url!",
    })
    assert status.startswith("400")


# ── reactions ─────────────────────────────────────────────────────────────────

TOKEN = "~AbCdEfGhIjK"


@pytest.mark.asyncio
async def test_sealed_dm_reaction_reaches_peer_as_token_plus_header(gw):
    a, b = _connect(gw, ALICE), _connect(gw, BOB)
    await gw._handle_add_reaction(a, {"cert_id": BOB, "message_id": "m-8", "emoji": TOKEN, **SEALED})
    ev = _events(b, "reaction_added")[-1]
    assert ev["emoji"] == TOKEN
    for k, v in SEALED.items():
        assert ev[k] == v
    rows = gw._store.get_reactions(BOB) if hasattr(gw._store, "get_reactions") else None
    if rows is not None:
        assert all(r.get("emoji") != "👍" for r in rows)


@pytest.mark.asyncio
async def test_sealed_reaction_relays_header(gw):
    a = _connect(gw, ALICE)
    gw._peer_gateway_urls[BOB] = "https://bob.example"
    gw._relay_ephemeral = AsyncMock()
    await gw._handle_add_reaction(a, {"cert_id": BOB, "message_id": "m-9", "emoji": TOKEN, **SEALED})
    import asyncio
    await asyncio.sleep(0)
    payload = gw._relay_ephemeral.call_args[0][1]
    assert payload["content_type"] == "dm_reaction" and payload["emoji"] == TOKEN
    for k, v in SEALED.items():
        assert payload[k] == v


@pytest.mark.asyncio
async def test_inbound_sealed_reaction_relay_delivers_header(gw):
    bob = _connect(gw, BOB)
    gw._authorized_relationship = lambda did: ({}, "our-cert")
    gw._store.get_relationship_owner = lambda did: BOB
    status, _ = await gw._handle_dm_reaction_relay({
        "from_webid": ALICE, "to_webid": BOB, "message_id": "m-10", "emoji": TOKEN,
        "action": "add", **SEALED,
    })
    assert status.startswith("200")
    ev = _events(bob, "reaction_added")[-1]
    assert ev["emoji"] == TOKEN and ev["ratchet_pub"] == SEALED["ratchet_pub"]


@pytest.mark.asyncio
async def test_sealed_reaction_in_room_rejected(gw):
    a = _connect(gw, ALICE)
    gw._local_rooms["room-r"] = {"name": "R", "code": "x", "members": {a}}
    await gw._handle_add_reaction(a, {"room_id": "room-r", "message_id": "m-11", "emoji": TOKEN, **SEALED})
    assert _events(a, "error")[-1]["message"] == "invalid_e2e_fields"
    assert not _events(a, "reaction_added")


@pytest.mark.asyncio
async def test_plain_room_reaction_unchanged(gw):
    a = _connect(gw, ALICE)
    gw._local_rooms["room-p"] = {"name": "R", "code": "x", "members": {a}}
    gw._store.add_room_member("room-p", ALICE)
    await gw._handle_add_reaction(a, {"room_id": "room-p", "message_id": "m-12", "emoji": "👍"})
    ev = _events(a, "reaction_added")[-1]
    assert ev["emoji"] == "👍" and "e2e" not in ev


# ── inline files ──────────────────────────────────────────────────────────────

# Random-looking ciphertext: no recognised magic, so the plaintext path would
# refuse it as application/octet-stream.
FILE_CT = base64.b64encode(bytes(range(17, 250)) * 3).decode()


@pytest.mark.asyncio
async def test_sealed_dm_file_forwarded_as_ciphertext(gw):
    a, b = _local_dm(gw)
    await gw._handle_send_file(a, {
        "cert_id": THREAD, "filename": "file", "mime_type": "application/octet-stream",
        "data_b64": FILE_CT, "width": 640, "height": 480, "spoiler": True, **SEALED,
    })
    ev = _events(b, "message")[-1]
    assert ev["content"] == CT
    assert ev["file"]["data_b64"] == FILE_CT
    assert ev["file"]["filename"] == "file"
    assert ev["file"]["mime_type"] == "application/octet-stream"
    # Dimensions and the spoiler flag belong inside the sealed header.
    assert "width" not in ev["file"] and ev["file"]["spoiler"] is False
    for k, v in HDR.items():
        assert ev[k] == v
    stored = gw._store.get_local_messages(THREAD) if hasattr(gw._store, "get_local_messages") else None
    if stored:
        assert stored[-1]["content"] == "📎"


@pytest.mark.asyncio
async def test_sealed_file_overrides_declared_name_and_type(gw):
    a, b = _local_dm(gw)
    await gw._handle_send_file(a, {
        "cert_id": THREAD, "filename": "holiday.jpg", "mime_type": "image/jpeg",
        "data_b64": FILE_CT, **SEALED,
    })
    f = _events(b, "message")[-1]["file"]
    assert f["filename"] == "file" and f["mime_type"] == "application/octet-stream"


@pytest.mark.asyncio
async def test_plain_octet_stream_file_still_refused(gw):
    a, b = _local_dm(gw)
    await gw._handle_send_file(a, {
        "cert_id": THREAD, "filename": "x.bin", "mime_type": "application/octet-stream",
        "data_b64": FILE_CT,
    })
    assert _events(a, "error")[-1]["message"].startswith("file_type_not_allowed")
    assert not _events(b, "message")


@pytest.mark.asyncio
async def test_sealed_file_still_checks_executable_magic(gw):
    a, b = _local_dm(gw)
    await gw._handle_send_file(a, {
        "cert_id": THREAD, "data_b64": base64.b64encode(b"MZ" + b"\x00" * 64).decode(), **SEALED,
    })
    assert "executable" in _events(a, "error")[-1]["message"]


@pytest.mark.asyncio
async def test_sealed_file_to_room_rejected(gw):
    a = _connect(gw, ALICE)
    gw._local_rooms["room-f"] = {"name": "R", "code": "x", "members": {a}}
    await gw._handle_send_file(a, {"room_id": "room-f", "data_b64": FILE_CT, **SEALED})
    assert _events(a, "error")[-1]["message"] == "e2e_dm_only"


@pytest.mark.asyncio
async def test_inbound_relay_keeps_sealed_file_header(gw):
    from proxion_messenger_core.relay import sign_relay_message
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from proxion_messenger_core.didkey import pub_key_to_did
    from datetime import datetime, timezone
    import secrets
    priv = Ed25519PrivateKey.generate()
    from_did = pub_key_to_did(priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    to_did = pub_key_to_did(b"\x07" * 32)
    ws = _connect(gw, to_did)
    ts = datetime.now(timezone.utc).isoformat()
    nonce = secrets.token_hex(8)
    sig = sign_relay_message(priv, from_did, to_did, "file-sealed-1", CT, ts, nonce)
    body = json.dumps({
        "from_webid": from_did, "to_webid": to_did, "message_id": "file-sealed-1",
        "content": CT, "timestamp": ts, "signature": sig, "relay_nonce": nonce,
        "file": {"filename": "file", "mime_type": "application/octet-stream",
                 "size": 10, "data_b64": FILE_CT},
        **HDR,
    }).encode()
    status, resp = await gw._handle_relay_post(body, client_ip="127.0.0.1")
    assert status.startswith("200"), resp
    ev = _events(ws, "message")[-1]
    assert ev["e2e_v"] == 1 and ev["ratchet_pub"] == SEALED["ratchet_pub"]
    assert ev["file"]["data_b64"] == FILE_CT


# ── chunked files ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sealed_file_offer_forwarded_with_placeholder_metadata(gw):
    a, b = _connect(gw, ALICE), _connect(gw, BOB)
    await gw._handle_file_offer(a, {
        "to_webid": BOB, "file_id": "f-s", "filename": "secret-plans.pdf",
        "mime_type": "application/pdf", "size_bytes": 900016, "total_chunks": 14, **SEALED,
    })
    ev = _events(b, "file_offer")[-1]
    assert ev["filename"] == "file" and ev["mime_type"] == "application/octet-stream"
    assert ev["size_bytes"] == 900016 and ev["total_chunks"] == 14
    for k, v in SEALED.items():
        assert ev[k] == v


@pytest.mark.asyncio
async def test_malformed_sealed_offer_rejected(gw):
    a, b = _connect(gw, ALICE), _connect(gw, BOB)
    await gw._handle_file_offer(a, {
        "to_webid": BOB, "file_id": "f-b", "size_bytes": 10, "total_chunks": 1,
        **SEALED, "msg_num": -4,
    })
    assert _events(a, "error")[-1]["message"] == "invalid_e2e_fields"
    assert not _events(b, "file_offer")


@pytest.mark.asyncio
async def test_plain_offer_still_needs_allowed_type(gw):
    a, b = _connect(gw, ALICE), _connect(gw, BOB)
    await gw._handle_file_offer(a, {
        "to_webid": BOB, "file_id": "f-p", "filename": "x.bin",
        "mime_type": "application/octet-stream", "size_bytes": 10, "total_chunks": 1,
    })
    assert _events(a, "error")[-1]["message"].startswith("file_type_not_allowed")


# Through process_command: the command schema (emoji length, content size) and
# dispatch accept the sealed forms, and the peer gets them unchanged.
@pytest.mark.asyncio
async def test_sealed_commands_pass_schema_and_dispatch(gw):
    a, b = _local_dm(gw)
    await gw.process_command(a, {"cmd": "edit_local_message", "message_id": "m-w1",
                                 "thread_id": THREAD, **SEALED})
    await gw.process_command(a, {"cmd": "add_reaction", "cert_id": BOB, "message_id": "m-w1",
                                 "emoji": TOKEN, **SEALED})
    await gw.process_command(a, {"cmd": "remove_reaction", "cert_id": BOB, "message_id": "m-w1",
                                 "emoji": TOKEN})
    await gw.process_command(a, {"cmd": "send_file", "cert_id": THREAD, "filename": "file",
                                 "mime_type": "application/octet-stream", "data_b64": FILE_CT, **SEALED})
    errors = [e for e in _events(a, "error")]
    assert not errors, errors
    assert _events(b, "message_edited")[-1]["new_content"] == CT
    assert _events(b, "reaction_added")[-1]["emoji"] == TOKEN
    assert _events(b, "reaction_removed")[-1]["emoji"] == TOKEN
    assert _events(b, "message")[-1]["e2e_v"] == 1


# The relay endpoint rejects unknown top-level keys before dispatch; handler-level
# tests above bypass that allow-list, so check it admits the sealed fields.
@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"content_type": "dm_edit", "new_content": CT, **HDR},
    {"content_type": "dm_reaction", "emoji": TOKEN, "action": "add", **SEALED},
])
async def test_relay_allowlist_admits_sealed_fields(gw, extra):
    body = json.dumps({"from_webid": ALICE, "to_webid": BOB, "message_id": "m-x", **extra}).encode()
    _status, resp = await gw._handle_relay_post(body, client_ip="127.0.0.1")
    assert "unknown_relay_fields" not in resp
