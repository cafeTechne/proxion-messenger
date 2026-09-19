"""MED-severity gateway hardening: bounded edit history, clamped disappear
timers, and an authz gate on the sender-key read path.

F1 — the per-message edit-history cap must hold at the STORE, where every edit
     path converges, not only in the room handler.
F2 — a disappearing-timer ms must be clamped so the expiry sweep's timedelta
     cannot overflow and abort the whole gateway-wide tick.
F3 — get_sender_key must require room membership and restrict the caller to its
     own sender_webid, mirroring the write siblings.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core._store.security import _MAX_EDITS_PER_MESSAGE
from proxion_messenger_core._gateway_rooms import _MAX_DISAPPEAR_MS, _disappear_cutoff


# ── shared fixtures / helpers ──────────────────────────────────────────────

@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = "https://alice.pod/profile/card#me"
    return a


@pytest.fixture
def gateway(agent):
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(), read_state=ReadState(),
    )


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


# ── F1: store-level edit-history cap ───────────────────────────────────────

def test_save_edit_ring_keeps_most_recent(store):
    """save_edit is the convergence point; past the cap it prunes the oldest
    rows and keeps the most recent _MAX_EDITS_PER_MESSAGE (a bounded ring)."""
    mid = "msg-ring"
    total = _MAX_EDITS_PER_MESSAGE + 50
    for i in range(total):
        store.save_edit(
            f"edit-{i:06d}", mid, f"prev-{i:04d}", f"edit-{i:04d}",
            "alice@example.org", f"2024-01-01T00:00:00.{i:06d}+00:00",
        )
    edits = store.get_edits(mid)
    assert len(edits) == _MAX_EDITS_PER_MESSAGE
    kept = {e["new_content"] for e in edits}
    # The newest survives, the oldest was pruned.
    assert "edit-0149" in kept
    assert "edit-0000" not in kept
    assert "edit-0049" not in kept  # first (total - cap) are gone
    assert "edit-0050" in kept


def test_update_message_edit_history_bounded(store):
    """The DM edit path (update_message → save_edit, fresh uuid each time) does
    not grow message_edits without bound."""
    mid = "msg-dm-spam"
    store.save_message(
        mid, "thread-1", "relay", "alice@example.org", "Alice",
        "original", datetime.now(timezone.utc).isoformat(),
    )
    for i in range(_MAX_EDITS_PER_MESSAGE + 30):
        store.update_message(
            mid, f"content-{i}",
            edited_at=f"2024-02-01T00:00:00.{i:06d}+00:00",
            editor_webid="alice@example.org",
        )
    assert len(store.get_edits(mid)) == _MAX_EDITS_PER_MESSAGE
    # The message content update itself still applied (latest wins).
    assert store.get_message(mid)["content"] == f"content-{_MAX_EDITS_PER_MESSAGE + 29}"


# ── F2: disappear-timer clamp + non-fatal sweep ────────────────────────────

def test_disappear_cutoff_clamps_and_never_overflows():
    """A poisoned (over-large) ms is clamped to a representable cutoff, never
    raising OverflowError; junk / non-positive values return None."""
    # A value far past the overflow threshold clamps instead of raising, landing
    # ~365 days in the past (the ceiling), not further.
    cutoff = _disappear_cutoff(10 ** 30)
    assert cutoff is not None
    floor = (datetime.now(timezone.utc)
             - timedelta(milliseconds=_MAX_DISAPPEAR_MS)
             - timedelta(minutes=1)).isoformat()
    assert cutoff >= floor  # clamped to the ceiling, not an astronomically old date
    assert _disappear_cutoff(0) is None
    assert _disappear_cutoff(-5) is None
    assert _disappear_cutoff("not-a-number") is None


@pytest.mark.asyncio
async def test_set_disappear_timer_clamps_huge_ms(gateway):
    """The setter clamps an over-large ms to _MAX_DISAPPEAR_MS."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "https://alice.pod/profile/card#me"
    room_id = "room-huge"
    gateway._local_rooms[room_id] = {
        "creator_webid": "https://alice.pod/profile/card#me",
        "members": {ws}, "messages": [], "history_mode": "none",
    }
    await gateway.process_command(ws, {
        "cmd": "set_disappear_timer", "room_id": room_id, "ms": 10 ** 18,
    })
    assert gateway._room_disappear_timers[room_id] == _MAX_DISAPPEAR_MS


@pytest.mark.asyncio
async def test_expire_loop_survives_poisoned_row(gateway):
    """A pre-existing poisoned timer row is not fatal: the sweep completes and
    other threads still expire."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "https://alice.pod/profile/card#me"

    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()

    # Poisoned room processed FIRST (insertion order): an over-large ms that
    # would overflow the raw timedelta. If it aborted the tick, the ok room
    # below would never be reached.
    poison_id = "room-poison"
    gateway._local_rooms[poison_id] = {
        "members": {ws}, "messages": [
            {"message_id": "p1", "content": "x", "timestamp": old_ts},
        ], "history_mode": "none",
    }
    gateway._room_disappear_timers[poison_id] = 10 ** 19

    ok_id = "room-ok"
    gateway._local_rooms[ok_id] = {
        "members": {ws}, "messages": [
            {"message_id": "ok1", "content": "y", "timestamp": old_ts},
        ], "history_mode": "none",
    }
    gateway._room_disappear_timers[ok_id] = 500  # 500ms

    sleep_count = 0

    async def fake_sleep(_):
        # Let a full tick complete (top sleep + one sleep(0) per room) before
        # cancelling at the start of the next tick, so both rooms are processed.
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 4:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await gateway._expire_messages_loop()

    # The legitimate thread expired despite the poisoned sibling.
    ok_ids = [m["message_id"] for m in gateway._local_rooms[ok_id]["messages"]]
    assert "ok1" not in ok_ids


# ── F3: sender-key read authz ──────────────────────────────────────────────

def _room_with_members(gateway, store, room_id, sockets):
    gateway._store = store
    gateway._force_auth = True
    gateway._local_rooms[room_id] = {
        "creator_webid": gateway._client_webids.get(next(iter(sockets)), ""),
        "members": set(sockets), "messages": [], "history_mode": "none",
    }


@pytest.mark.asyncio
async def test_get_sender_key_member_own(gateway, store):
    """A current member may fetch its OWN sender key."""
    alice = _mock_ws()
    gateway._client_webids[alice] = "alice@example.org"
    _room_with_members(gateway, store, "room-sk", {alice})
    store.save_sender_key("room-sk", "alice@example.org", "ck_alice==", 0)

    await gateway.process_command(alice, {
        "cmd": "get_sender_key", "room_id": "room-sk",
        "sender_webid": "alice@example.org",
    })
    resp = json.loads(alice.send.call_args[0][0])
    assert resp["type"] == "sender_key"
    assert resp["key"] is not None
    assert resp["key"]["chain_key_b64"] == "ck_alice=="


@pytest.mark.asyncio
async def test_get_sender_key_non_member_refused(gateway, store):
    """A non-member (kicked or never-joined) cannot pull the raw chain key."""
    alice = _mock_ws()
    carol = _mock_ws()
    gateway._client_webids[alice] = "alice@example.org"
    gateway._client_webids[carol] = "carol@example.org"
    _room_with_members(gateway, store, "room-sk2", {alice})  # carol NOT a member
    store.save_sender_key("room-sk2", "alice@example.org", "ck_alice==", 0)

    await gateway.process_command(carol, {
        "cmd": "get_sender_key", "room_id": "room-sk2",
        "sender_webid": "carol@example.org",
    })
    resp = json.loads(carol.send.call_args[0][0])
    assert resp["type"] == "error"


@pytest.mark.asyncio
async def test_get_sender_key_other_member_refused(gateway, store):
    """A member may NOT fetch another member's raw sender key."""
    alice = _mock_ws()
    bob = _mock_ws()
    gateway._client_webids[alice] = "alice@example.org"
    gateway._client_webids[bob] = "bob@example.org"
    _room_with_members(gateway, store, "room-sk3", {alice, bob})
    store.save_sender_key("room-sk3", "bob@example.org", "ck_bob==", 0)

    await gateway.process_command(alice, {
        "cmd": "get_sender_key", "room_id": "room-sk3",
        "sender_webid": "bob@example.org",
    })
    resp = json.loads(alice.send.call_args[0][0])
    assert resp["type"] == "error"
