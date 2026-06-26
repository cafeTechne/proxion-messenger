"""Round 3: import_data guardrails — messages, content, timestamps."""
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_import_rejects_oversized_message_content(store):
    """Messages with content >16 KiB are skipped on import."""
    big_content = "x" * (16 * 1024 + 1)
    messages = [{
        "message_id": "big-msg-001",
        "thread_id": "room-1",
        "thread_type": "room",
        "from_webid": "did:key:alice",
        "content": big_content,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }]
    result = store.import_data({"messages": messages})
    assert result["messages"] == 0, "Oversized message should be skipped"


def test_import_rejects_invalid_timestamp_rows(store):
    """Messages with unparseable timestamps are skipped on import."""
    messages = [{
        "message_id": "bad-ts-001",
        "thread_id": "room-1",
        "thread_type": "room",
        "from_webid": "did:key:alice",
        "content": "hello",
        "timestamp": "NOT-A-DATE",
    }]
    result = store.import_data({"messages": messages})
    assert result["messages"] == 0, "Invalid timestamp message should be skipped"


def test_import_accepts_valid_messages(store):
    """Valid messages with proper timestamps are imported."""
    messages = [{
        "message_id": "ok-msg-001",
        "thread_id": "room-1",
        "thread_type": "room",
        "from_webid": "did:key:alice",
        "content": "hello world",
        "timestamp": "2026-01-01T12:00:00+00:00",
    }]
    result = store.import_data({"messages": messages})
    assert result["messages"] == 1, f"Valid message should be imported: {result}"


def test_import_rejects_too_many_messages(store):
    """Stops importing messages after MAX_IMPORT_MESSAGES."""
    messages = [
        {
            "message_id": f"msg-{i:05d}",
            "thread_id": "room-bulk",
            "thread_type": "room",
            "from_webid": "did:key:alice",
            "content": f"message {i}",
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        for i in range(10002)
    ]
    result = store.import_data({"messages": messages})
    assert result["messages"] <= 10000


def test_import_rejects_too_many_relationships(store):
    """Stops importing relationships after MAX_IMPORT_RELATIONSHIPS (2000)."""
    rels = [
        {
            "certificate_id": f"cert-{i:04d}",
            "peer_pub_hex": "aabbccdd" * 8,
            "peer_did": f"did:key:peer{i}",
            "cert_json": "{}",
            "created_at": 0,
            "expires_at": 0,
        }
        for i in range(2001)
    ]
    result = store.import_data({"relationships": rels})
    assert result["relationships"] <= 2000
