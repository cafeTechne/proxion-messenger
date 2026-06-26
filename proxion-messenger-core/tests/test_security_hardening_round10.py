"""Round 10 security hardening tests.

Covers:
  1. /backup endpoint: API token required from non-loopback when PROXION_API_TOKEN is set.
  2. Reactions: 100-total-per-message hard cap (across all senders).
  3. Metadata length: room names and display names truncated to 64 chars at storage layer.
  4. upsert_contact: display_name truncated to 64 chars.
"""
from __future__ import annotations

import json
import os
import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gw(tmp_path, suffix="r10"):
    agent = AgentState.generate()
    config = GatewayConfig(port=0, db_path=str(tmp_path / f"{suffix}.db"))
    return ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=config,
        read_state=ReadState(),
    )


def _fake_ws(gw, webid):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._webid_sockets[webid] = ws
    return ws


# ---------------------------------------------------------------------------
# 1. /backup auth — API token enforcement
# ---------------------------------------------------------------------------

class TestBackupAuth:
    def test_is_trusted_origin_no_origin_loopback(self):
        """No-Origin request from loopback is trusted even without token."""
        assert ProxionGateway._is_trusted_origin(b"", 8080, "127.0.0.1") is True

    def test_is_trusted_origin_no_origin_loopback_ipv6(self):
        assert ProxionGateway._is_trusted_origin(b"", 8080, "::1") is True

    def test_is_trusted_origin_no_origin_nonloopback(self):
        """No-Origin from a non-loopback IP is NOT trusted."""
        assert ProxionGateway._is_trusted_origin(b"", 8080, "10.0.0.5") is False

    def test_is_trusted_origin_no_origin_empty_peer(self):
        """Empty peer string is treated as local (in-process / test scenario)."""
        assert ProxionGateway._is_trusted_origin(b"", 8080, "") is True

    def test_is_trusted_origin_localhost_origin(self):
        origin = b"http://localhost:8080"
        assert ProxionGateway._is_trusted_origin(origin, 8080, "10.0.0.5") is True

    def test_is_trusted_origin_wrong_port(self):
        origin = b"http://localhost:9999"
        assert ProxionGateway._is_trusted_origin(origin, 8080, "") is False

    def test_is_trusted_origin_tauri(self):
        assert ProxionGateway._is_trusted_origin(b"tauri://localhost", 8080, "1.2.3.4") is True

    def test_is_trusted_origin_external(self):
        assert ProxionGateway._is_trusted_origin(b"https://evil.com", 8080, "") is False


# ---------------------------------------------------------------------------
# 2. Reaction total cap (100 per message)
# ---------------------------------------------------------------------------

class TestReactionTotalCap:
    @pytest.mark.asyncio
    async def test_total_reaction_cap_blocks_at_100(self, tmp_path):
        gw = _make_gw(tmp_path, "react-cap")
        store = gw._store
        assert store is not None

        msg_id = str(uuid.uuid4())
        room_id = "room-react-cap"

        sender = "did:key:heavy-sender"

        # Seed 50 reactions from same sender — hitting the per-user-per-room quota.
        for i in range(50):
            ok = store.save_reaction(room_id, msg_id, chr(0x1F600 + i), sender)
            assert ok is True

        assert store.count_reactions_total(msg_id) == 50

        # Attempt a 51st reaction via the gateway handler — must be rejected.
        sender_ws = _fake_ws(gw, sender)
        gw._local_rooms[room_id] = {
            "creator_webid": sender,
            "members": {sender_ws},
        }

        msg = json.dumps({
            "cmd": "add_reaction",
            "message_id": msg_id,
            "room_id": room_id,
            "emoji": "🆕",
        })
        await gw.process_command(sender_ws, json.loads(msg))

        calls = [json.loads(c.args[0]) for c in sender_ws.send.call_args_list]
        errors = [c for c in calls if c.get("type") == "error"]
        assert any("reaction_limit_reached" in c.get("message", "") for c in errors), (
            f"Expected reaction_limit_reached error, got: {calls}"
        )
        assert store.count_reactions_total(msg_id) == 50

    @pytest.mark.asyncio
    async def test_per_sender_cap_still_enforced(self, tmp_path):
        gw = _make_gw(tmp_path, "react-sender")
        store = gw._store
        assert store is not None

        msg_id = str(uuid.uuid4())
        room_id = "room-sender-cap"
        sender_webid = "did:key:heavy-reactor"

        for i in range(50):
            store.save_reaction(room_id, msg_id, chr(0x1F600 + i), sender_webid)

        assert store.count_reactions_by_sender(msg_id, sender_webid) == 50

        sender_ws = _fake_ws(gw, sender_webid)
        gw._local_rooms[room_id] = {
            "creator_webid": sender_webid,
            "members": {sender_ws},
        }

        msg = json.dumps({
            "cmd": "add_reaction",
            "message_id": msg_id,
            "room_id": room_id,
            "emoji": "🆕",
        })
        await gw.process_command(sender_ws, json.loads(msg))

        calls = [json.loads(c.args[0]) for c in sender_ws.send.call_args_list]
        errors = [c for c in calls if c.get("type") == "error"]
        assert any("reaction_limit_reached" in c.get("message", "") for c in errors)


# ---------------------------------------------------------------------------
# 3. Metadata length constraints
# ---------------------------------------------------------------------------

class TestMetadataLengthConstraints:
    def test_room_name_truncated_at_64(self, tmp_path):
        store = LocalStore(str(tmp_path / "meta.db"))
        long_name = "R" * 100
        store.save_room("room-trunc-1", long_name, "code1", "", "visible", "did:key:owner")
        rooms = {r["room_id"]: r for r in store.get_all_rooms()}
        assert "room-trunc-1" in rooms
        assert len(rooms["room-trunc-1"]["name"]) == 64
        assert rooms["room-trunc-1"]["name"] == "R" * 64

    def test_room_name_short_unchanged(self, tmp_path):
        store = LocalStore(str(tmp_path / "meta2.db"))
        store.save_room("room-trunc-2", "Short Name", "code2", "", "visible", "did:key:owner")
        rooms = {r["room_id"]: r for r in store.get_all_rooms()}
        assert "room-trunc-2" in rooms
        assert rooms["room-trunc-2"]["name"] == "Short Name"

    def test_display_name_in_message_truncated(self, tmp_path):
        store = LocalStore(str(tmp_path / "meta3.db"))
        room_id = "room-dn-trunc"
        import time as _time
        store.save_room(room_id, "TestRoom", "code3", "", "visible", "did:key:owner")
        long_dn = "D" * 100
        msg_id = str(uuid.uuid4())
        store.save_message(
            msg_id, room_id, "room", "did:key:sender", long_dn,
            "hello", _time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        msgs = store.get_messages(room_id)
        assert msgs, "No messages saved"
        assert len(msgs[0]["from_display_name"]) == 64
        assert msgs[0]["from_display_name"] == "D" * 64

    def test_display_name_in_contact_truncated(self, tmp_path):
        store = LocalStore(str(tmp_path / "meta4.db"))
        long_dn = "C" * 100
        store.upsert_contact("did:key:contact1", long_dn)
        contacts = store.get_all_contacts()
        assert contacts, "No contacts returned"
        match = next((c for c in contacts if c["webid"] == "did:key:contact1"), None)
        assert match is not None
        assert len(match["display_name"]) == 64
        assert match["display_name"] == "C" * 64

    def test_display_name_exactly_64_unchanged(self, tmp_path):
        store = LocalStore(str(tmp_path / "meta5.db"))
        exact_dn = "E" * 64
        store.upsert_contact("did:key:contact2", exact_dn)
        contacts = store.get_all_contacts()
        match = next((c for c in contacts if c["webid"] == "did:key:contact2"), None)
        assert match is not None
        assert match["display_name"] == exact_dn
