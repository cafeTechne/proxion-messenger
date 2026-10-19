"""Deleting a message, expiring a thread or deleting a room removes every row
that hangs off it (pins, edits, reactions, receipts, files, invites, roles...)."""
from __future__ import annotations

from proxion_messenger_core.local_store import LocalStore

ROOM = "room-1"
OTHER = "room-2"
ALICE = "did:key:zAlice"
BOB = "did:key:zBob"


def _count(store, table, col, val) -> int:
    with store._conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (val,)).fetchone()[0]


def _message_with_dependents(store, thread_id, mid, ts="2026-01-01T00:00:00+00:00"):
    store.save_message(mid, thread_id, "room", ALICE, "Alice", "secret " + mid, ts)
    store.save_pin(thread_id, mid, ALICE, "secret " + mid)
    store.save_reaction(thread_id, mid, "+1", BOB)
    store.save_edit("e-" + mid, mid, "old", "secret " + mid, ALICE, ts)
    store.save_receipt(mid, BOB, delivered_at=ts, read_at=ts)
    store.save_message_file(mid, thread_id, "a.txt", "text/plain", b"filebytes")


def _dependents(store, mid) -> dict:
    return {t: _count(store, t, "message_id", mid)
            for t in ("pins", "reactions", "message_edits", "message_receipts", "message_files")}


def test_delete_message_removes_reactions_and_other_dependents(tmp_path):
    store = LocalStore(tmp_path / "s.db")
    _message_with_dependents(store, ROOM, "m1")
    assert all(_dependents(store, "m1").values())
    store.delete_message("m1")
    assert _dependents(store, "m1") == dict.fromkeys(_dependents(store, "m1"), 0)


def test_disappearing_expiry_without_pod_removes_dependents(tmp_path):
    store = LocalStore(tmp_path / "s.db")
    _message_with_dependents(store, ROOM, "old", ts="2026-01-01T00:00:00+00:00")
    _message_with_dependents(store, ROOM, "new", ts="2026-06-01T00:00:00+00:00")
    assert store.delete_messages_before(ROOM, "2026-03-01T00:00:00+00:00") == 1
    assert set(_dependents(store, "old").values()) == {0}
    # The message newer than the cutoff keeps everything.
    assert all(_dependents(store, "new").values())


def test_delete_room_removes_everything_the_room_owns(tmp_path):
    store = LocalStore(tmp_path / "s.db")
    for rid in (ROOM, OTHER):
        store.save_room(rid, "r", "code-" + rid, "", "all", ALICE)
        store.add_room_member(rid, BOB)
        _message_with_dependents(store, rid, "m-" + rid)
        store.set_room_role(rid, BOB, "moderator")
        store.ban_room_member(rid, "did:key:zEve", ALICE, "spam")
        store.mute_room_member(rid, "did:key:zMallory", ALICE)
        store.save_room_emoji(rid, "party", "image/png", "aGk=", ALICE)
        store.create_room_invite("inv-" + rid, rid, "hash-" + rid, uses_left=5)
        store.add_federated_room_member(rid, "did:key:zCarol", "https://gw.example")
        store.create_webhook({"id": "wh-" + rid, "thread_id": rid, "owner_webid": ALICE,
                              "direction": "incoming", "token": "tok-" + rid,
                              "created_at": "2026-01-01T00:00:00+00:00"})

    store.delete_room(ROOM)

    room_tables = ("rooms", "room_members", "room_roles", "room_bans", "room_mutes",
                   "room_emoji", "room_invites", "room_federated_members", "reactions")
    for t in room_tables:
        assert _count(store, t, "room_id", ROOM) == 0, t
        assert _count(store, t, "room_id", OTHER) >= 1, t
    for t in ("messages", "pins", "message_files", "webhooks"):
        assert _count(store, t, "thread_id", ROOM) == 0, t
    assert set(_dependents(store, "m-" + ROOM).values()) == {0}
    assert all(_dependents(store, "m-" + OTHER).values())
    # A live invite code for the deleted room no longer resolves.
    assert store.consume_room_invite("hash-" + ROOM) is None
    assert store.get_webhook_by_token("tok-" + ROOM) is None
