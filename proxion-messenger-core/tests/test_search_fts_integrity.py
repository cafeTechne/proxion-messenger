"""Search index integrity and query safety (F7).

The FTS table is external-content over ``messages``: it must track edits and
deletes exactly, the user's text must never be parsed as FTS5 syntax, and an
empty thread scope must match nothing rather than every thread.
"""
import sqlite3

import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "fts.db"))


def _save(store, mid, content, thread="t1"):
    store.save_message(mid, thread, "room", "did:key:zA", "Alice", content,
                       "2026-10-01T10:00:00Z")


def _ids(rows):
    return [r["message_id"] for r in rows]


def _fts_token_hits(store, token):
    """Rowids the raw index holds for ``token`` (bypasses the messages join)."""
    with sqlite3.connect(store.db_path) as conn:
        return [r[0] for r in conn.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?", (f'"{token}"',))]


def test_edited_out_text_no_longer_matches(store):
    _save(store, "m1", "launch codes alpha")
    store.update_message("m1", "nothing to see")
    assert _ids(store.search_messages("alpha")) == []
    assert _ids(store.search_messages("nothing")) == ["m1"]


def test_deleted_after_edit_leaves_no_index_entry(store):
    _save(store, "m1", "launch codes alpha")
    store.update_message("m1", "nothing to see")
    store.delete_message("m1")
    assert _fts_token_hits(store, "alpha") == []
    assert _fts_token_hits(store, "nothing") == []


def test_reused_rowid_does_not_inherit_stale_hits(store):
    _save(store, "m1", "keep")
    _save(store, "m2", "secretword original")
    store.update_message("m2", "edited text")
    store.delete_message("m2")
    # The highest rowid was freed, so the next insert reuses it.
    _save(store, "m3", "unrelated words", thread="t2")
    assert _ids(store.search_messages("secretword")) == []


@pytest.mark.parametrize("query,content", [
    ("e-mail", "please e-mail me"),
    ("v1.2", "released v1.2 today"),
    ("don't", "I don't know"),
    ('say "hi"', 'say "hi" back'),
    ("NOT", "this is NOT a drill"),
    ("a AND", "a AND b"),
    ("col:on", "col:on value"),
    ("(paren", "(paren text"),
    ("star*", "star* power"),
])
def test_punctuation_and_operators_are_literal(store, query, content):
    _save(store, "m1", content)
    _save(store, "m2", "something else entirely")
    assert _ids(store.search_messages(query)) == ["m1"]


def test_punctuation_only_query_returns_nothing(store):
    _save(store, "m1", "hello")
    assert store.search_messages("-") == []
    assert store.search_messages('"') == []


def test_empty_scope_matches_nothing(store):
    _save(store, "m1", "hello other account", thread="someone-elses-thread")
    assert store.search_messages("hello", []) == []
    assert _ids(store.search_messages("hello", ["someone-elses-thread"])) == ["m1"]


def test_migration_rebuilds_a_corrupted_index(tmp_path):
    db = str(tmp_path / "old.db")
    s = LocalStore(db)
    _save(s, "m1", "launch codes alpha")
    # Reproduce the pre-62 state: no update trigger, then an edit.
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TRIGGER IF EXISTS messages_au")
        conn.execute("UPDATE messages SET content = 'nothing to see' WHERE message_id = 'm1'")
        conn.execute("UPDATE schema_version SET version = 61")
    assert _fts_token_hits(s, "alpha")  # stale entry present
    s2 = LocalStore(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 62
    assert _fts_token_hits(s2, "alpha") == []
    assert _ids(s2.search_messages("nothing")) == ["m1"]
    s2.update_message("m1", "fresh words")
    assert _ids(s2.search_messages("fresh")) == ["m1"]
    assert _ids(s2.search_messages("nothing")) == []


def test_rebuild_messages_fts_clears_stale_entries(store):
    _save(store, "m1", "launch codes alpha")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER IF EXISTS messages_au")
        conn.execute("UPDATE messages SET content = 'nothing' WHERE message_id = 'm1'")
    store.rebuild_messages_fts()
    assert _fts_token_hits(store, "alpha") == []
    assert _ids(store.search_messages("nothing")) == ["m1"]
