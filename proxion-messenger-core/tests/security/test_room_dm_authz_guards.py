"""Authorization guards that every role, sender and contact check must keep.

Each test names the check it pins. They drive commands through
process_command (the wire path), and where a command has two layers (the
pre-dispatch ACL in gateway.process_command and the handler's own check) a
second test calls the handler directly so removing either layer fails a test.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

ROOM = "room-authz-guards"


def _new_did() -> str:
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _sent(ws) -> list[dict]:
    return [json.loads(c.args[0]) for c in ws.send.call_args_list]


def _types(ws) -> list[str]:
    return [m.get("type") for m in _sent(ws)]


def _last(ws) -> dict:
    return json.loads(ws.send.call_args.args[0])


@pytest.fixture
def gw(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "authz.db")),
    )


async def _register(gw, did):
    ws = _mock_ws()
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": did[-6:]})
    ws.send.reset_mock()
    return ws


@pytest.fixture
async def room(gw):
    """Owner, a plain member and a target member in one local room, plus an
    outsider who is registered but not in the room."""
    people = {}
    for name in ("owner", "member", "target", "outsider"):
        did = _new_did()
        people[name] = (did, await _register(gw, did))
    owner_did = people["owner"][0]
    gw._local_rooms[ROOM] = {
        "name": "Guards", "code": "guards", "invite_url": "",
        "members": {people[n][1] for n in ("owner", "member", "target")},
        "creator_webid": owner_did,
    }
    gw._room_codes["guards"] = ROOM
    for n in ("owner", "member", "target"):
        gw._store.add_room_member(ROOM, people[n][0])
    return people


def _reset(people):
    for _did, ws in people.values():
        ws.send.reset_mock()


# ── delete_room ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_room_by_member_refused_at_dispatch(gw, room):
    _did, member = room["member"]
    await gw.process_command(member, {"cmd": "delete_room", "room_id": ROOM})
    assert _last(member) == {"type": "error", "code": "E_FORBIDDEN", "message": "not_room_owner"}
    assert ROOM in gw._local_rooms


@pytest.mark.asyncio
async def test_delete_room_handler_checks_owner(gw, room):
    _did, member = room["member"]
    _reset(room)
    await gw._handle_delete_room(member, {"room_id": ROOM})
    assert _last(member)["type"] == "error"
    assert ROOM in gw._local_rooms
    assert "room_deleted" not in _types(room["target"][1])


@pytest.mark.asyncio
async def test_delete_room_by_owner_works(gw, room):
    _did, owner = room["owner"]
    await gw.process_command(owner, {"cmd": "delete_room", "room_id": ROOM})
    assert ROOM not in gw._local_rooms
    assert "room_deleted" in _types(room["member"][1])


# ── transfer_ownership / accept_ownership ─────────────────────────────────────

@pytest.mark.asyncio
async def test_transfer_by_member_refused_at_dispatch(gw, room):
    target_did, target = room["target"]
    _did, member = room["member"]
    await gw.process_command(member, {"cmd": "transfer_ownership", "room_id": ROOM, "to_did": target_did})
    assert _last(member) == {"type": "error", "code": "E_FORBIDDEN", "message": "not_room_owner"}
    assert ROOM not in gw._pending_ownership_transfers
    assert "ownership_transfer_offer" not in _types(target)


@pytest.mark.asyncio
async def test_transfer_handler_checks_owner(gw, room):
    target_did, target = room["target"]
    _did, member = room["member"]
    await gw._handle_transfer_ownership(member, {"room_id": ROOM, "to_did": target_did})
    assert _last(member)["type"] == "error"
    assert ROOM not in gw._pending_ownership_transfers
    assert "ownership_transfer_offer" not in _types(target)


@pytest.mark.asyncio
async def test_accept_ownership_only_by_the_offered_member(gw, room):
    owner_did, owner = room["owner"]
    target_did, target = room["target"]
    member_did, member = room["member"]
    await gw.process_command(owner, {"cmd": "transfer_ownership", "room_id": ROOM, "to_did": target_did})
    assert "ownership_transfer_offer" in _types(target)

    # Someone else in the room tries to take the pending transfer.
    await gw.process_command(member, {"cmd": "accept_ownership", "room_id": ROOM})
    assert _last(member)["type"] == "error"
    assert gw._local_rooms[ROOM]["creator_webid"] == owner_did
    assert ROOM in gw._pending_ownership_transfers

    # The offered member can.
    await gw.process_command(target, {"cmd": "accept_ownership", "room_id": ROOM})
    assert gw._local_rooms[ROOM]["creator_webid"] == target_did


# ── send_room ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_room_by_outsider_refused_at_dispatch(gw, room):
    _did, outsider = room["outsider"]
    await gw.process_command(outsider, {"cmd": "send_room", "room_id": ROOM, "content": "intrude"})
    last = _last(outsider)
    assert (last["code"], last["message"]) == ("E_FORBIDDEN", "not_room_member")
    assert gw._store.get_messages(ROOM) == []


@pytest.mark.asyncio
async def test_send_room_handler_checks_membership(gw, room):
    _did, outsider = room["outsider"]
    _reset(room)
    await gw._handle_send_room(outsider, {"room_id": ROOM, "content": "intrude"})
    assert _last(outsider)["type"] == "error"
    assert gw._store.get_messages(ROOM) == []
    for n in ("owner", "member", "target"):
        assert not any(m.get("content") == "intrude" for m in _sent(room[n][1]))


# ── moderation: mute / unmute / unban / get_room_bans ────────────────────────

@pytest.mark.asyncio
async def test_mute_by_plain_member_refused(gw, room):
    target_did, _t = room["target"]
    _did, member = room["member"]
    await gw.process_command(member, {"cmd": "mute_member", "room_id": ROOM, "webid": target_did})
    assert _last(member) == {"type": "error", "message": "insufficient_permissions"}
    assert gw._store.is_room_muted(ROOM, target_did) is False


@pytest.mark.asyncio
async def test_unmute_by_plain_member_refused(gw, room):
    owner_did, _o = room["owner"]
    target_did, _t = room["target"]
    _did, member = room["member"]
    gw._store.mute_room_member(ROOM, target_did, owner_did)
    await gw.process_command(member, {"cmd": "unmute_member", "room_id": ROOM, "webid": target_did})
    assert _last(member) == {"type": "error", "message": "insufficient_permissions"}
    assert gw._store.is_room_muted(ROOM, target_did) is True


@pytest.mark.asyncio
async def test_unban_by_plain_member_refused(gw, room):
    owner_did, _o = room["owner"]
    banned = _new_did()
    _did, member = room["member"]
    gw._store.ban_room_member(ROOM, banned, owner_did)
    await gw.process_command(member, {"cmd": "unban_member", "room_id": ROOM, "webid": banned})
    assert _last(member) == {"type": "error", "message": "insufficient_permissions"}
    assert gw._store.is_room_banned(ROOM, banned) is True


@pytest.mark.asyncio
async def test_get_room_bans_by_plain_member_refused(gw, room):
    owner_did, owner = room["owner"]
    banned = _new_did()
    _did, member = room["member"]
    gw._store.ban_room_member(ROOM, banned, owner_did, reason="spam")
    await gw.process_command(member, {"cmd": "get_room_bans", "room_id": ROOM})
    assert "room_bans" not in _types(member)
    assert _last(member) == {"type": "error", "message": "insufficient_permissions"}

    await gw.process_command(owner, {"cmd": "get_room_bans", "room_id": ROOM})
    assert [b["banned_did"] for b in _last(owner)["bans"]] == [banned]


# ── edit / delete another member's message ───────────────────────────────────

def _seed_message(gw, sender_did, content="original"):
    mid = str(uuid.uuid4())
    gw._store.save_message(mid, ROOM, "room", sender_did, "Owner", content,
                           datetime.now(timezone.utc).isoformat())
    return mid


@pytest.mark.asyncio
async def test_member_cannot_delete_another_members_message(gw, room):
    owner_did, _o = room["owner"]
    _did, member = room["member"]
    mid = _seed_message(gw, owner_did)
    await gw.process_command(member, {"cmd": "delete_local_message", "message_id": mid, "thread_id": ROOM})
    assert _last(member)["type"] == "error"
    assert gw._store.get_message(mid) is not None
    assert "message_deleted" not in _types(room["target"][1])


@pytest.mark.asyncio
async def test_member_cannot_edit_another_members_message(gw, room):
    owner_did, _o = room["owner"]
    _did, member = room["member"]
    mid = _seed_message(gw, owner_did)
    await gw.process_command(member, {"cmd": "edit_local_message", "message_id": mid,
                                      "thread_id": ROOM, "content": "forged"})
    assert _last(member)["type"] == "error"
    assert gw._store.get_message(mid)["content"] == "original"
    assert "message_edited" not in _types(room["target"][1])


# ── webhooks ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_webhook_is_owner_scoped(gw, room):
    owner_did, owner = room["owner"]
    _did, member = room["member"]
    wh_id = str(uuid.uuid4())
    gw._store.create_webhook({
        "id": wh_id, "thread_id": ROOM, "owner_webid": owner_did, "direction": "incoming",
        "token": "tok-" + wh_id, "url": "", "bot_name": "bot", "created_at": 0,
    })
    await gw.process_command(member, {"cmd": "delete_webhook", "id": wh_id})
    assert _last(member)["type"] == "error"
    assert [h["id"] for h in gw._store.get_webhooks_for_thread(ROOM, "incoming")] == [wh_id]

    await gw.process_command(owner, {"cmd": "delete_webhook", "id": wh_id})
    assert _last(owner)["type"] == "webhook_deleted"
    assert gw._store.get_webhooks_for_thread(ROOM, "incoming") == []


# ── voice relay from a revoked or blocked contact ────────────────────────────

async def _voice_contact(gw):
    me = _new_did()
    ws = await _register(gw, me)
    peer = _new_did()
    gw._store.save_relationship(
        {"certificate_id": "c-" + peer[-6:], "subject": "ab" * 32,
         "created_at": 0, "expires_at": 2**31 - 1},
        peer_did=peer, owner_webid=me,
    )
    return me, ws, peer


def _offer(me, peer):
    return {"to_webid": me, "from_webid": peer, "signal_type": "ice_candidate",
            "session_id": "s-" + peer[-4:], "signal_data": {"candidate": "a=x"}}


@pytest.mark.asyncio
async def test_voice_relay_from_contact_delivers(gw):
    me, ws, peer = await _voice_contact(gw)
    status, _ = await gw._handle_voice_signal_relay(_offer(me, peer))
    assert status == "200 OK"
    assert "voice_signal" in _types(ws)


@pytest.mark.asyncio
async def test_voice_relay_from_revoked_contact_dropped(gw):
    me, ws, peer = await _voice_contact(gw)
    gw._revoked_dids.add(peer)
    status, _ = await gw._handle_voice_signal_relay(_offer(me, peer))
    assert status == "202 Accepted"
    assert "voice_signal" not in _types(ws)


@pytest.mark.asyncio
async def test_voice_relay_from_blocked_contact_dropped(gw):
    me, ws, peer = await _voice_contact(gw)
    gw._store.set_block(me, peer, True)
    status, _ = await gw._handle_voice_signal_relay(_offer(me, peer))
    assert status == "202 Accepted"
    assert "voice_signal" not in _types(ws)


# ── DMs to a revoked contact / from a blocked sender ─────────────────────────

@pytest.mark.asyncio
async def test_local_dm_to_revoked_contact_refused(gw):
    alice = await _register(gw, _new_did())
    bob_did = _new_did()
    bob = await _register(gw, bob_did)
    gw._revoked_dids.add(bob_did)
    await gw.process_command(alice, {"cmd": "local_dm", "target_webid": bob_did, "content": "after revoke"})
    assert _last(alice)["message"] == "contact_revoked"
    assert not any(m.get("content") == "after revoke" for m in _sent(bob))
    assert not any(m.get("content") == "after revoke"
                   for m in gw._store.get_messages(bob_did) + gw._store.get_messages(gw._client_webids[alice]))


@pytest.mark.asyncio
async def test_sealed_dm_from_blocked_sender_not_delivered(gw):
    alice_did = _new_did()
    alice = await _register(gw, alice_did)
    bob_did = _new_did()
    bob = await _register(gw, bob_did)
    gw._store.set_block(bob_did, alice_did, True)
    await gw.process_command(alice, {"cmd": "sealed_dm", "target_webid": bob_did, "sealed_b64": "c2VhbGVk"})
    # The sender sees the same ack either way, so the block is not revealed.
    assert _last(alice)["type"] == "sealed_dm_sent"
    assert "sealed_message" not in _types(bob)

    gw._store.set_block(bob_did, alice_did, False)
    await gw.process_command(alice, {"cmd": "sealed_dm", "target_webid": bob_did, "sealed_b64": "c2VhbGVk"})
    assert "sealed_message" in _types(bob)
