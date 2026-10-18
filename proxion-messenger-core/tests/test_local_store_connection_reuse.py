"""LocalStore keeps one sqlite connection per thread instead of one per call.

Opening a connection and setting WAL on every store call capped a room at
roughly 27 messages a second. These tests pin the connection count and the
transaction semantics the per-call connections used to give for free.
"""
import sqlite3
import threading

import pytest

from proxion_messenger_core._store import _base
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def connect_counter(monkeypatch):
    calls = []
    real = sqlite3.connect

    def counting(*a, **k):
        calls.append(threading.get_ident())
        return real(*a, **k)

    monkeypatch.setattr(_base.sqlite3, "connect", counting)
    return calls


def _save(store, i, thread="room-1"):
    store.save_message(f"m{i}", thread, "room", "did:a", "A", f"hi {i}",
                       f"2026-01-01T00:00:{i % 60:02d}.{i:06d}+00:00")


def test_store_calls_reuse_one_connection_per_thread(tmp_path, connect_counter):
    store = LocalStore(str(tmp_path / "s.db"))
    for i in range(50):
        _save(store, i)
        store.set_last_read("did:a", "room-1")
        store.increment_room_unread("room-1", ["did:b", "did:c"])
        store.get_messages("room-1", limit=10)
    assert len(store.get_messages("room-1", limit=100)) == 50
    # 200+ store calls, one connection.
    assert len(connect_counter) == 1


def test_each_thread_gets_its_own_connection(tmp_path, connect_counter):
    store = LocalStore(str(tmp_path / "s.db"))
    errors = []

    def worker(n):
        try:
            for i in range(20):
                _save(store, n * 100 + i, thread=f"t{n}")
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    for n in range(4):
        assert len(store.get_messages(f"t{n}", limit=100)) == 20
    assert len(set(connect_counter)) == 5  # main thread + 4 workers
    assert len(connect_counter) == 5


def test_with_block_rolls_back_on_error(tmp_path):
    store = LocalStore(str(tmp_path / "s.db"))
    with pytest.raises(RuntimeError):
        with store._conn() as conn:
            conn.execute("INSERT INTO display_names (webid, display_name, updated_at) VALUES ('w', 'n', 0)")
            raise RuntimeError("boom")
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM display_names").fetchone()[0] == 0


def test_transaction_groups_calls_and_rolls_back_together(tmp_path):
    store = LocalStore(str(tmp_path / "s.db"))
    with pytest.raises(RuntimeError):
        with store.transaction():
            _save(store, 1)
            store.set_last_read("did:a", "room-1")
            raise RuntimeError("boom")
    assert store.get_messages("room-1", limit=10) == []

    with store.transaction():
        _save(store, 2)
        # A nested block must not commit early: another connection cannot
        # see the row until the outer block exits.
        other = sqlite3.connect(store.db_path)
        assert other.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert other.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    other.close()


def test_uncommitted_stray_write_does_not_hold_the_write_lock(tmp_path):
    store = LocalStore(str(tmp_path / "s.db"))
    # A handle used without ``with`` and never committed used to be closed
    # (and rolled back) as soon as it was dropped. Keep that.
    store._conn().execute(
        "INSERT INTO display_names (webid, display_name, updated_at) VALUES ('w', 'n', 0)")
    other = sqlite3.connect(store.db_path, timeout=0.2)
    other.execute("INSERT INTO display_names (webid, display_name, updated_at) VALUES ('x', 'y', 0)")
    other.commit()
    other.close()
    with store._conn() as conn:
        rows = [r[0] for r in conn.execute("SELECT webid FROM display_names")]
    assert rows == ["x"]


def test_close_releases_connections_and_store_reopens(tmp_path, connect_counter):
    store = LocalStore(str(tmp_path / "s.db"))
    _save(store, 1)
    store.close()
    _save(store, 2)
    assert len(store.get_messages("room-1", limit=10)) == 2
    assert len(connect_counter) == 2


def test_security_event_cursor_does_not_skip_same_tick_events(tmp_path):
    # Faster store calls made same-timestamp events common; the cursor must
    # still return every event exactly once.
    store = LocalStore(str(tmp_path / "s.db"))
    for i in range(5):
        store.save_security_event(f"e{i}", "info", details=str(i))
    with store._conn() as conn:
        conn.execute("UPDATE security_events SET created_at = 1000.0")
    first = store.get_security_events_after(cursor="", limit=2)
    rest = store.get_security_events_after(cursor=first[-1]["id"], limit=10)
    ids = [e["id"] for e in first + rest]
    assert len(ids) == 5 and len(set(ids)) == 5
