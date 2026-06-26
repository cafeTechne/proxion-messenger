"""Tests for proxion_messenger_core.solid_store — Solid Pod backed store implementation."""

from unittest.mock import MagicMock, call
import json
import pytest

from proxion_messenger_core.sealed import SealedEnvelope
from proxion_messenger_core.solid_client import SolidClient, SolidError
from proxion_messenger_core.solid_store import SolidStore
from proxion_messenger_core.store import StoredMessage


@pytest.fixture
def mock_client():
    """Mock SolidClient."""
    return MagicMock(spec=SolidClient)


@pytest.fixture
def store(mock_client):
    """SolidStore instance with mocked client."""
    return SolidStore(mock_client)


@pytest.fixture
def sample_envelope():
    """Sample SealedEnvelope for testing."""
    return SealedEnvelope(
        ephemeral_pub=b"ephemeral_pub_bytes_32",
        nonce=b"nonce_12bytes",
        ciphertext=b"ciphertext_data",
    )


# ---------------------------------------------------------------------------
# put() tests
# ---------------------------------------------------------------------------


def test_put_writes_json_to_correct_pod_path(store, mock_client, sample_envelope):
    """put() writes JSON to correct pod path and returns message_id."""
    mailbox_id = "abc123"

    # When put is called, capture what was written
    written_data = None

    def capture_put(path, data, **kwargs):
        nonlocal written_data
        written_data = json.loads(data.decode("utf-8"))

    mock_client.put.side_effect = capture_put

    message_id = store.put(mailbox_id, sample_envelope)

    # Verify put was called with correct path
    call_args = mock_client.put.call_args
    assert call_args[0][0] == f"stash://handshake/{mailbox_id}/{message_id}.json"

    # Verify JSON structure
    assert written_data["message_id"] == message_id
    assert written_data["envelope"]["@type"] == "SealedEnvelope"
    assert "posted_at" in written_data
    assert isinstance(written_data["posted_at"], float)


def test_put_returns_unique_message_ids(store, mock_client, sample_envelope):
    """put() returns unique message_ids on successive calls."""
    mailbox_id = "abc123"
    mock_client.put.return_value = None

    id1 = store.put(mailbox_id, sample_envelope)
    id2 = store.put(mailbox_id, sample_envelope)

    assert id1 != id2
    assert len(id1) > 0
    assert len(id2) > 0


# ---------------------------------------------------------------------------
# list_all() tests
# ---------------------------------------------------------------------------


def test_list_all_returns_stored_messages(store, mock_client, sample_envelope):
    """list_all() fetches and deserializes messages from pod."""
    mailbox_id = "abc123"

    # Mock list to return two message filenames
    msg1_uri = "stash://handshake/abc123/msg1.json"
    msg2_uri = "stash://handshake/abc123/msg2.json"
    mock_client.list.return_value = [msg1_uri, msg2_uri]

    # Mock get to return valid JSON for each message
    def mock_get(uri):
        if uri == msg1_uri:
            data = {
                "message_id": "msg1",
                "envelope": sample_envelope.to_dict(),
                "posted_at": 1234567890.0,
            }
        elif uri == msg2_uri:
            data = {
                "message_id": "msg2",
                "envelope": sample_envelope.to_dict(),
                "posted_at": 1234567900.0,
            }
        return json.dumps(data).encode("utf-8")

    mock_client.get.side_effect = mock_get

    messages = store.list_all(mailbox_id)

    assert len(messages) == 2
    assert messages[0].message_id == "msg1"
    assert messages[1].message_id == "msg2"
    assert isinstance(messages[0].envelope, SealedEnvelope)
    assert isinstance(messages[1].envelope, SealedEnvelope)
    assert messages[0].posted_at == 1234567890.0
    assert messages[1].posted_at == 1234567900.0


def test_list_all_returns_empty_on_404(store, mock_client):
    """list_all() returns [] on 404 from pod."""
    mailbox_id = "abc123"

    # Mock list to raise 404
    error = SolidError("not found", status_code=404)
    mock_client.list.side_effect = error

    messages = store.list_all(mailbox_id)

    assert messages == []


def test_list_all_skips_malformed_json(store, mock_client, sample_envelope):
    """list_all() silently skips files that fail to parse."""
    mailbox_id = "abc123"

    # Mock list to return three filenames
    valid_uri = "stash://handshake/abc123/valid.json"
    bad_json_uri = "stash://handshake/abc123/bad.json"
    non_json_uri = "stash://handshake/abc123/notjson.txt"

    mock_client.list.return_value = [valid_uri, bad_json_uri, non_json_uri]

    # Mock get to return valid JSON for valid_uri, bad JSON for bad_json_uri
    def mock_get(uri):
        if uri == valid_uri:
            data = {
                "message_id": "valid",
                "envelope": sample_envelope.to_dict(),
                "posted_at": 1234567890.0,
            }
            return json.dumps(data).encode("utf-8")
        elif uri == bad_json_uri:
            return b"not valid json"
        else:
            # non-JSON file is skipped by extension check
            return b"{}"

    mock_client.get.side_effect = mock_get

    messages = store.list_all(mailbox_id)

    # Only the valid message should be returned
    assert len(messages) == 1
    assert messages[0].message_id == "valid"


# ---------------------------------------------------------------------------
# take_by_ids() tests
# ---------------------------------------------------------------------------


def test_take_by_ids_deletes_only_matching(store, mock_client, sample_envelope):
    """take_by_ids() deletes only messages matching the provided IDs."""
    mailbox_id = "abc123"

    # Mock list_all to return two messages
    msg1 = StoredMessage(
        message_id="msg1", envelope=sample_envelope, posted_at=1234567890.0
    )
    msg2 = StoredMessage(
        message_id="msg2", envelope=sample_envelope, posted_at=1234567900.0
    )

    def mock_list_all(mid):
        if mid == mailbox_id:
            return [msg1, msg2]
        return []

    # Mock list to return empty (we're bypassing it via list_all mock in integration)
    mock_client.list.return_value = []
    mock_client.get.side_effect = Exception("should not be called")

    # We need to mock list_all at the store level for testing
    store.list_all = MagicMock(return_value=[msg1, msg2])

    removed = store.take_by_ids(mailbox_id, {"msg1"})

    # Verify only msg1 was deleted
    assert len(removed) == 1
    assert removed[0].message_id == "msg1"

    # Verify delete was called only for msg1
    mock_client.delete.assert_called_once_with(
        f"stash://handshake/{mailbox_id}/msg1.json"
    )


def test_take_by_ids_returns_correct_order(store, mock_client, sample_envelope):
    """take_by_ids() returns messages in storage order."""
    mailbox_id = "abc123"

    msg1 = StoredMessage(
        message_id="msg1", envelope=sample_envelope, posted_at=1000.0
    )
    msg2 = StoredMessage(
        message_id="msg2", envelope=sample_envelope, posted_at=2000.0
    )
    msg3 = StoredMessage(
        message_id="msg3", envelope=sample_envelope, posted_at=3000.0
    )

    store.list_all = MagicMock(return_value=[msg1, msg2, msg3])

    removed = store.take_by_ids(mailbox_id, {"msg1", "msg3"})

    assert len(removed) == 2
    assert removed[0].message_id == "msg1"
    assert removed[1].message_id == "msg3"


# ---------------------------------------------------------------------------
# take_all() tests
# ---------------------------------------------------------------------------


def test_take_all_deletes_all(store, mock_client, sample_envelope):
    """take_all() deletes all messages."""
    mailbox_id = "abc123"

    # Mock list_all to return three messages
    msg1 = StoredMessage(
        message_id="msg1", envelope=sample_envelope, posted_at=1000.0
    )
    msg2 = StoredMessage(
        message_id="msg2", envelope=sample_envelope, posted_at=2000.0
    )
    msg3 = StoredMessage(
        message_id="msg3", envelope=sample_envelope, posted_at=3000.0
    )

    store.list_all = MagicMock(return_value=[msg1, msg2, msg3])

    messages = store.take_all(mailbox_id)

    assert len(messages) == 3
    assert messages[0].message_id == "msg1"
    assert messages[1].message_id == "msg2"
    assert messages[2].message_id == "msg3"

    # Verify delete was called once for each message
    assert mock_client.delete.call_count == 3
    mock_client.delete.assert_any_call(
        f"stash://handshake/{mailbox_id}/msg1.json"
    )
    mock_client.delete.assert_any_call(
        f"stash://handshake/{mailbox_id}/msg2.json"
    )
    mock_client.delete.assert_any_call(
        f"stash://handshake/{mailbox_id}/msg3.json"
    )


def test_take_all_returns_empty_when_mailbox_empty(store, mock_client):
    """take_all() returns empty list when mailbox has no messages."""
    mailbox_id = "abc123"

    store.list_all = MagicMock(return_value=[])

    messages = store.take_all(mailbox_id)

    assert messages == []
    mock_client.delete.assert_not_called()
