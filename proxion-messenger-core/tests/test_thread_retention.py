"""A full thread keeps accepting new messages; retention prunes the oldest."""
import sqlite3

from proxion_messenger_core._store import messages as msgmod
from proxion_messenger_core.local_store import LocalStore


def _fill(store, thread_id, n, prefix="f"):
    with sqlite3.connect(store.db_path) as c:
        c.executemany(
            "INSERT INTO messages (message_id, thread_id, thread_type, from_webid, content, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            [(f"{prefix}{i}", thread_id, "room", "did:key:a", "hi", "2026-01-01T00:00:00+00:00")
             for i in range(n)],
        )


def _count(store, thread_id):
    with sqlite3.connect(store.db_path) as c:
        return c.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?", (thread_id,)).fetchone()[0]


def test_message_saved_after_5000_in_thread(tmp_path):
    st = LocalStore(str(tmp_path / "s.db"))
    _fill(st, "room-x", 5000)
    st.save_message("newest", "room-x", "room", "did:key:a", "A", "after 5000",
                    "2026-10-16T00:00:00+00:00")
    st.save_voice_message("voice-newest", "room-x", "room", "did:key:a", "A", "QUJD", 900,
                          "2026-10-16T00:00:01+00:00")
    assert st.get_message("newest") is not None
    assert st.get_message("voice-newest") is not None


def test_retention_prunes_oldest_with_dependents(tmp_path, monkeypatch):
    monkeypatch.setattr(msgmod, "THREAD_RETENTION_MESSAGES", 10)
    monkeypatch.setattr(msgmod, "THREAD_RETENTION_CHECK_EVERY", 1)
    st = LocalStore(str(tmp_path / "s.db"))
    for i in range(10):
        st.save_message(f"m{i}", "room-x", "room", "did:key:a", "A", f"msg {i}",
                        f"2026-01-01T00:00:{i:02d}+00:00")
    st.save_pin("room-x", "m0", "did:key:a", "msg 0")
    st.save_reaction("room-x", "m0", "+1", "did:key:a")
    st.update_message("m0", "edited", editor_webid="did:key:a")
    # An unrelated thread is untouched.
    st.save_message("other", "room-y", "room", "did:key:a", "A", "x", "2025-01-01T00:00:00+00:00")

    # A backdated newcomer is still kept: pruning follows insertion order.
    st.save_message("late", "room-x", "room", "did:key:a", "A", "late",
                    "2020-01-01T00:00:00+00:00")
    assert st.get_message("late") is not None
    assert st.get_message("m0") is None
    assert _count(st, "room-x") == 10
    assert st.get_message("other") is not None
    with sqlite3.connect(st.db_path) as c:
        for table in ("pins", "reactions", "message_edits"):
            assert c.execute(f"SELECT COUNT(*) FROM {table} WHERE message_id = 'm0'").fetchone()[0] == 0


def test_retention_byte_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(msgmod, "THREAD_RETENTION_BYTES", 100)
    monkeypatch.setattr(msgmod, "THREAD_RETENTION_CHECK_EVERY", 1)
    st = LocalStore(str(tmp_path / "s.db"))
    for i in range(5):
        st.save_message(f"b{i}", "t", "room", "did:key:a", "A", "x" * 40,
                        f"2026-01-01T00:00:0{i}+00:00")
    assert st.get_message("b4") is not None
    assert st.get_message("b0") is None
    with sqlite3.connect(st.db_path) as c:
        total = c.execute("SELECT SUM(LENGTH(content)) FROM messages WHERE thread_id='t'").fetchone()[0]
    assert total <= 100


def test_prune_is_not_run_on_every_insert(tmp_path, monkeypatch):
    calls = []
    st = LocalStore(str(tmp_path / "s.db"))
    orig = st.prune_thread
    monkeypatch.setattr(st, "prune_thread", lambda tid, *a, **k: (calls.append(tid), orig(tid))[1])
    for i in range(msgmod.THREAD_RETENTION_CHECK_EVERY + 1):
        st.save_message(f"p{i}", "t", "room", "did:key:a", "A", "x", "2026-01-01T00:00:00+00:00")
    assert len(calls) == 2


def test_import_not_capped_at_5000(tmp_path):
    st = LocalStore(str(tmp_path / "s.db"))
    _fill(st, "dm-x", 5000)
    res = st.import_data({"messages": [{
        "message_id": "imp-1", "thread_id": "dm-x", "thread_type": "relay",
        "from_webid": "did:key:a", "content": "restored", "timestamp": "2026-02-01T00:00:00+00:00",
    }]})
    assert res["messages"] == 1
    assert st.get_message("imp-1") is not None


def test_retention_prunes_stored_room_files(tmp_path, monkeypatch):
    monkeypatch.setattr(msgmod, "THREAD_RETENTION_MESSAGES", 2)
    monkeypatch.setattr(msgmod, "THREAD_RETENTION_CHECK_EVERY", 1)
    st = LocalStore(str(tmp_path / "s.db"))
    st.save_message("f0", "room-x", "room", "did:key:a", "A", "📎 a.png",
                    "2026-01-01T00:00:00+00:00")
    assert st.save_message_file("f0", "room-x", "a.png", "image/png", b"\x89PNG")
    for i in range(1, 3):
        st.save_message(f"m{i}", "room-x", "room", "did:key:a", "A", "x",
                        f"2026-01-01T00:00:0{i}+00:00")
    assert st.get_message("f0") is None
    assert st.get_message_file("f0") is None
