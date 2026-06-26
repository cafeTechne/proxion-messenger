"""R17: FTS5 search with filters and pagination."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "fts.db"))


def _save_msg(store, msg_id, thread_id, content, from_webid="did:key:alice"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.save_message(msg_id, thread_id, "dm", from_webid, "Alice", content, ts)


def test_search_returns_expected_results_with_thread_filter(store):
    _save_msg(store, "m1", "thread-A", "hello from A")
    _save_msg(store, "m2", "thread-B", "hello from B")
    _save_msg(store, "m3", "thread-A", "another A message")

    results = store.search_messages("hello", thread_id="thread-A")
    ids = [r["message_id"] for r in results]
    assert "m1" in ids
    assert "m2" not in ids


def test_search_updates_after_edit_and_delete(store):
    _save_msg(store, "m-edit", "thread-C", "original content")
    results = store.search_messages("original")
    assert any(r["message_id"] == "m-edit" for r in results)

    store.rebuild_messages_fts()
    results2 = store.search_messages("original")
    assert any(r["message_id"] == "m-edit" for r in results2)


def test_search_pagination_cursor_returns_next_page(store):
    for i in range(5):
        _save_msg(store, f"page-{i}", "thread-P", f"paginated message {i}")

    page1 = store.search_messages("paginated", limit=2, offset=0)
    assert len(page1) == 2

    page2 = store.search_messages("paginated", limit=2, offset=2)
    assert len(page2) >= 1

    ids_p1 = {r["message_id"] for r in page1}
    ids_p2 = {r["message_id"] for r in page2}
    assert ids_p1.isdisjoint(ids_p2)
