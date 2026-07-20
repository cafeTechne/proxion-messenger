"""Tests: custom room emoji (R59G) — CRUD, validation, authz, federation relay."""
from __future__ import annotations
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore

OWNER = "did:key:zOwner"
MEMBER = "did:key:zMember"

# 1x1 valid PNG (magic bytes matter for the payload validator).
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg==")
PNG_B64 = base64.b64encode(PNG).decode()
WEBP_B64 = base64.b64encode(b"RIFF" + (20).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 20).decode()


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9992, db_path=str(tmp_path / "t.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "t.db"))
    return gw


def _ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    return ws


def _room(gw, ws, webid=OWNER, room_id="room-emoji-test"):
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._local_rooms[room_id] = {
        "name": "Emoji Test", "code": "x" * 64,
        "members": {ws}, "invite_url": "",
        "history_mode": "none", "messages": [],
        "creator_webid": OWNER,
    }
    return room_id


def _last_json(ws):
    return json.loads(ws.send.call_args[0][0])


# ── CRUD via WS commands ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_add_list_remove_emoji(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    await gateway._handle_add_room_emoji(ws, {
        "room_id": rid, "name": "partyparrot", "mime": "image/png", "data_b64": PNG_B64})
    # broadcast carries the updated list
    assert _last_json(ws)["type"] == "room_emoji"
    assert [e["name"] for e in _last_json(ws)["emoji"]] == ["partyparrot"]

    await gateway._handle_list_room_emoji(ws, {"room_id": rid})
    listed = _last_json(ws)
    assert listed["emoji"][0]["mime"] == "image/png"
    assert listed["emoji"][0]["data_b64"] == PNG_B64

    await gateway._handle_remove_room_emoji(ws, {"room_id": rid, "name": "partyparrot"})
    assert _last_json(ws)["emoji"] == []


@pytest.mark.asyncio
async def test_non_admin_member_cannot_add_or_remove(gateway):
    owner_ws = _ws()
    rid = _room(gateway, owner_ws)
    member_ws = _ws()
    gateway.clients.add(member_ws)
    gateway._client_webids[member_ws] = MEMBER
    gateway._local_rooms[rid]["members"].add(member_ws)

    await gateway._handle_add_room_emoji(member_ws, {
        "room_id": rid, "name": "sneaky", "mime": "image/png", "data_b64": PNG_B64})
    assert _last_json(member_ws)["message"] == "insufficient_permissions"
    assert gateway._store.get_room_emoji(rid) == []

    # but they CAN list
    await gateway._handle_list_room_emoji(member_ws, {"room_id": rid})
    assert _last_json(member_ws)["type"] == "room_emoji"


@pytest.mark.asyncio
async def test_validation_rejects_bad_payloads(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    cases = [
        ({"name": "Bad Name!", "mime": "image/png", "data_b64": PNG_B64}, "invalid_emoji_name"),
        ({"name": "x", "mime": "image/png", "data_b64": PNG_B64}, "invalid_emoji_name"),
        ({"name": "ok_name", "mime": "image/svg+xml", "data_b64": PNG_B64}, "emoji_type_not_allowed"),
        ({"name": "ok_name", "mime": "image/png", "data_b64": "!!notb64!!"}, "invalid_emoji_data"),
        ({"name": "ok_name", "mime": "image/png",
          "data_b64": base64.b64encode(b"\x89PNG\r\n" + b"\x00" * (65 * 1024)).decode()}, "emoji_too_large"),
        ({"name": "ok_name", "mime": "image/png",
          "data_b64": base64.b64encode(b"MZ\x90\x00 not an image").decode()}, "emoji_content_mismatch"),
    ]
    for payload, expected in cases:
        await gateway._handle_add_room_emoji(ws, {"room_id": rid, **payload})
        assert _last_json(ws)["message"] == expected, payload["name"]
    assert gateway._store.get_room_emoji(rid) == []


@pytest.mark.asyncio
async def test_per_room_cap_enforced(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    for i in range(gateway._EMOJI_MAX_PER_ROOM):
        gateway._store.save_room_emoji(rid, f"e{i:02d}", "image/png", PNG_B64, OWNER)
    await gateway._handle_add_room_emoji(ws, {
        "room_id": rid, "name": "onemore", "mime": "image/png", "data_b64": PNG_B64})
    assert _last_json(ws)["message"] == "emoji_limit_reached"
    # Replacing an EXISTING name is still allowed at the cap
    await gateway._handle_add_room_emoji(ws, {
        "room_id": rid, "name": "e00", "mime": "image/webp", "data_b64": WEBP_B64})
    assert _last_json(ws)["type"] == "room_emoji"


# ── Federation relay (inbound) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inbound_relay_applies_authorized_add(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    status, _ = await gateway._handle_room_emoji_relay({
        "content_type": "room_emoji", "action": "add", "room_id": rid,
        "name": "fedmoji", "mime": "image/webp", "data_b64": WEBP_B64,
        "from_webid": OWNER,
    })
    assert status.startswith("200")
    assert [e["name"] for e in gateway._store.get_room_emoji(rid)] == ["fedmoji"]
    # local members were notified
    assert _last_json(ws)["type"] == "room_emoji"


@pytest.mark.asyncio
async def test_inbound_relay_rejects_non_admin_caller(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    status, body = await gateway._handle_room_emoji_relay({
        "content_type": "room_emoji", "action": "add", "room_id": rid,
        "name": "evil", "mime": "image/png", "data_b64": PNG_B64,
        "from_webid": MEMBER,
    })
    assert status.startswith("403")
    assert gateway._store.get_room_emoji(rid) == []


@pytest.mark.asyncio
async def test_inbound_relay_revalidates_payload(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    status, body = await gateway._handle_room_emoji_relay({
        "content_type": "room_emoji", "action": "add", "room_id": rid,
        "name": "bad", "mime": "image/png",
        "data_b64": base64.b64encode(b"MZ evil").decode(),
        "from_webid": OWNER,
    })
    assert status.startswith("400")
    assert "emoji_content_mismatch" in body


@pytest.mark.asyncio
async def test_inbound_relay_remove(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    gateway._store.save_room_emoji(rid, "gone", "image/png", PNG_B64, OWNER)
    status, _ = await gateway._handle_room_emoji_relay({
        "content_type": "room_emoji", "action": "remove", "room_id": rid,
        "name": "gone", "from_webid": OWNER,
    })
    assert status.startswith("200")
    assert gateway._store.get_room_emoji(rid) == []


# ── Outbound relay + join sync ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_relays_delta_to_federated_gateways(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    gateway._store.add_federated_room_member(rid, MEMBER, "https://peer.example")
    sent = []
    gateway._relay_ephemeral = AsyncMock(side_effect=lambda gw_url, payload: sent.append((gw_url, payload)))
    await gateway._handle_add_room_emoji(ws, {
        "room_id": rid, "name": "fanout", "mime": "image/png", "data_b64": PNG_B64})
    # task scheduled — let it run
    import asyncio
    await asyncio.sleep(0)
    assert sent and sent[0][0] == "https://peer.example"
    assert sent[0][1]["content_type"] == "room_emoji"
    assert sent[0][1]["action"] == "add"
    assert sent[0][1]["data_b64"] == PNG_B64


@pytest.mark.asyncio
async def test_join_sync_replays_existing_set_to_one_gateway(gateway):
    ws = _ws()
    rid = _room(gateway, ws)
    gateway._store.save_room_emoji(rid, "a_first", "image/png", PNG_B64, OWNER)
    gateway._store.save_room_emoji(rid, "b_second", "image/webp", WEBP_B64, OWNER)
    gateway._store.add_federated_room_member(rid, MEMBER, "https://newpeer.example")
    gateway._store.add_federated_room_member(rid, "did:key:zOther", "https://old.example")
    sent = []
    gateway._relay_ephemeral = AsyncMock(side_effect=lambda gw_url, payload: sent.append((gw_url, payload)))
    gateway._sync_room_emoji_to_gateway(rid, "https://newpeer.example")
    import asyncio
    await asyncio.sleep(0)
    targets = {g for g, _ in sent}
    assert targets == {"https://newpeer.example"}   # only the new gateway
    assert sorted(p["name"] for _, p in sent) == ["a_first", "b_second"]
